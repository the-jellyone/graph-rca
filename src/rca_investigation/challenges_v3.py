"""Declared frozen-method replay perturbations, never additional physical trials."""
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
from .contracts import canonical, digest
from .catalogue import catalogue_hash
from .dataset import atomic_json, read_split, load_public_bundle, load_private_label
from .backends.replay import ReplayBackend
from .environment import InvestigationEnv, score_decision
from .evaluate_decisions import investigate, summarize_sets
from .comparison_v3 import load_frozen, policy_from_definition, file_hash
from .state_vector import StateVectorSchema
from .policies.dp import BoundedDP


OUTAGES = {'logs': {'logs'}, 'request_metrics': {'request_errors', 'latency'},
           'container_stats': {'cpu', 'memory'},
           'all_telemetry': {'logs', 'request_errors', 'latency', 'cpu', 'memory'}}


def perturb(specs, packets, scenario):
    altered = specs
    if scenario in OUTAGES:
        failed = {s.action_id for s in specs if s.template in OUTAGES[scenario]}
        packets = tuple(replace(p, availability='ERROR', response_code='UNAVAILABLE',
            evidence_json='{}', reason='synthetic_source_outage') if p.action_id in failed else p for p in packets)
    elif scenario in ('half-prices', 'double-prices'):
        factor = .5 if scenario == 'half-prices' else 2
        altered = tuple(replace(s, credits=max(1, int(s.credits * factor))) for s in specs)
    else:
        raise ValueError('undeclared v3 perturbation')
    return altered, packets


def price_policy(definition, policy, altered, model, config):
    """Keep learned values/weights and scales; expose changed public prices."""
    if definition['kind'] == 'dqn':
        schema = policy.schema.values
        schema['prices'] = {s.action_id: s.credits for s in altered}
        schema['catalogue_hash'] = catalogue_hash(altered)
        if max(schema['prices'].values()) > schema['scales']['price']:
            raise ValueError('declared price challenge exceeds frozen feature scale')
        policy.schema = StateVectorSchema(canonical(schema))
    elif definition['kind'] in ('dp', 'voi', 'expanded-rule'):
        policy = policy_from_definition(definition, model, altered, config)
    elif definition['kind'] == 'hybrid':
        objective = config.values['objective']
        policy.planner = BoundedDP(model, altered, depth=2, max_expanded_states=50000,
            correct=objective['correct'], incorrect=objective['incorrect'], defer=objective['defer'])
    return policy


def mixture_summary(outcomes, mixture):
    buckets = {'healthy': [], 'singleton': [], 'pair': []}
    for row in outcomes:
        buckets['healthy' if not row['truth'] else 'pair' if len(row['truth']) == 2 else 'singleton'].append(row)
    if not np.isclose(sum(mixture.values()), 1) or any(v < 0 for v in mixture.values()):
        raise ValueError('invalid declared operating mixture')
    if any(mixture[k] and not buckets[k] for k in buckets):
        raise ValueError('mixture requires measured outcomes for each weighted group')
    return {field: sum(mixture[k] * float(np.mean([row[field] for row in rows]))
            for k, rows in buckets.items() if mixture[k])
            for field in ('correct', 'return_value', 'credits', 'probes')}


def pair_boundary(outcomes, model, specs):
    """Separate absent evidence from an agent failing to acquire its tools."""
    pair = ('resource/catalogue-db', 'resource/payment')
    columns = [s.action_id for s in specs if s.resource_id == 'payment' and s.template in ('cpu', 'logs', 'status')]
    rows = [row for row in outcomes if tuple(row['truth']) == pair]
    return {'physical_pair_incidents': len(rows), 'second_root_tool_candidates': columns,
        'outcomes': [{'incident_id': row['incident_id'], 'correct': row['correct'],
            'relevant_tool_bought': any(p['action_id'] in columns for p in row['packets']),
            'relevant_tool_available': any(p['action_id'] in columns and p['availability'] == 'OK' for p in row['packets']),
            'full_evidence_correct': row['full_evidence_correct'],
            'diagnosed_causes': row['decision']['cause_ids']} for row in rows]}


def challenge_report_v3(dataset, specs, model, config, frozen_path, destination):
    frozen = load_frozen(frozen_path, dataset, config, model)
    root = Path(config.values['paths']['runs'])
    primary = json.loads((root / 'compare-test.json').read_text())
    if not primary['passed'] or primary['frozen_hash'] != frozen['hash']:
        raise ValueError('complete the sealed primary comparison before robustness work')
    declaration_path = Path('experiments/v3/robustness-declaration.json')
    declaration = json.loads(declaration_path.read_text())
    if declaration['source_outages'] != list(OUTAGES) or declaration['probe_price_multipliers'] != [.5, 2.]:
        raise ValueError('robustness matrix differs from implemented declaration')
    manifest, paths = read_split(dataset, 'test')
    identity = {'frozen_hash': frozen['hash'], 'split_hash': manifest['hash'],
                'declaration_sha256': file_hash(declaration_path), 'primary_sha256': file_hash(root / 'compare-test.json')}
    report = {'passed': False, 'identity': identity, 'synthetic_perturbations_only': True,
        'additional_physical_incidents': 0, 'priors_changed': False, 'retrained': False,
        'integer_credit_floor': 1, 'definitions': frozen['definitions'], 'reports': {}}
    destination = Path(destination)
    if destination.exists():
        report = json.loads(destination.read_text())
        if report['identity'] != identity: raise ValueError('partial robustness identity changed')
        if report['passed']: return report
    for scenario in (*OUTAGES, 'half-prices', 'double-prices'):
        methods = report['reports'].setdefault(scenario, {})
        for name, definition in frozen['definitions'].items():
            if name in methods: continue
            policy = policy_from_definition(definition, model, specs, config)
            # All incidents have the same catalogue under this declared scenario.
            altered, _ = perturb(specs, (), scenario)
            if scenario.endswith('prices'): policy = price_policy(definition, policy, altered, model, config)
            outcomes = []
            for path in paths:
                public, topology, packets = load_public_bundle(path, specs)
                altered, packets = perturb(specs, packets, scenario)
                public = replace(public, catalogue_hash=catalogue_hash(altered))
                objective = config.values['objective']
                env = InvestigationEnv(topology, altered, model, ReplayBackend(packets, altered),
                    correct_utility=objective['correct'], incorrect_utility=objective['incorrect'], defer_utility=objective['defer'])
                try:
                    env.reset(public)
                    row = investigate(env, policy, digest({'frozen': frozen['hash'], 'method': name, 'scenario': scenario}))
                    truth = load_private_label(path).cause_ids
                    receipt = json.loads((path / 'capture_receipt.json').read_text())
                    key = max(model.classes, key=model.diagnosis(tuple((p.action_id, p.symbol) for p in packets)).get)
                    row.update(incident_id=public.incident_id, truth=truth, capture_session=receipt['capture_session'],
                        stratum=receipt['stratum'], correct=env.decision.decision_type == 'DIAGNOSE' and env.decision.cause_ids == truth,
                        covered=env.decision.decision_type != 'DEFER', full_evidence_correct=model.decode(key) == truth,
                        return_value=row['probe_return'] + score_decision(env.decision, truth,
                            correct=objective['correct'], incorrect=objective['incorrect'], defer=objective['defer']))
                    outcomes.append(row)
                finally: env.close()
            summary = summarize_sets(outcomes, model.classes)
            summary['source_snapshots'] = summary.pop('physical_incidents')
            methods[name] = {'summary': summary, 'outcomes': outcomes}
            atomic_json(destination, report)
    report['mixtures'] = {name: [dict(mixture=m, metrics=mixture_summary(rows, m)) for m in declaration['mixtures']]
        for name, rows in primary['outcomes'].items()} if config.values['task_id'] == 'sockshop_multicause_v3' else {}
    report['physical_pair_boundary'] = {}
    for name, rows in primary['outcomes'].items():
        # Full evidence is evaluated on the original coherent pair, never joined singletons.
        by_id = {p.name: p for p in paths}
        pairs = []
        for row in rows:
            if len(row['truth']) != 2: continue
            _, _, packets = load_public_bundle(by_id[row['incident_id']], specs)
            probabilities = model.diagnosis(tuple((p.action_id, p.symbol) for p in packets))
            pairs.append({**row, 'full_evidence_correct': model.decode(max(model.classes, key=probabilities.get)) == tuple(row['truth'])})
        report['physical_pair_boundary'][name] = pair_boundary(pairs, model, specs)
    report['passed'] = True
    atomic_json(destination, report)
    return report
