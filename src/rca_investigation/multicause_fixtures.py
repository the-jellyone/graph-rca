"""An end-to-end synthetic pair with paid v2 evidence and v3 decisions."""
from .contracts import canonical, digest
from .contracts_v2 import ToolSpecV2, ToolResultV2, VOCABULARIES
from .contracts_v3 import IncidentPublicV3, ObjectiveV3
from .diagnosis_sets import PAIR
from .belief_v3 import fit_set_model
from .catalogue import catalogue_hash
from .topology import sockshop_topology
from .backends.replay import ReplayBackend
from .environment import InvestigationEnv, score_decision
from .policies.dp import BoundedDP
from .evaluate_decisions import investigate
from .explain import explain_result


def pair_environment(row=3, budget=10, horizon=5):
    topology = sockshop_topology()
    encoder = digest('synthetic_v3_status_encoder')
    specs = (ToolSpecV2('status/catalogue-db/v2', 'catalogue-db', 'status', 2, '{}', 1, ('status',),
                       VOCABULARIES['status'], encoder),
             ToolSpecV2('cpu/payment/v2', 'payment', 'cpu', 2, canonical({'window_seconds': 120}), 1, ('cpu',),
                       VOCABULARIES['cpu'], encoder))
    incident = IncidentPublicV3('opaque-fixture', topology.hash, catalogue_hash(specs), 'front-end',
        'manual_frontend', '2026-10-14T10:00:00Z', '2026-10-14T10:02:00Z', budget, horizon, .05,
        encoding_hash=encoder, objective_hash=digest(ObjectiveV3().to_dict()))
    labels = ((), ('resource/catalogue-db',), ('resource/payment',), PAIR)
    records = []
    for index, label in enumerate(labels):
        packets = []
        for spec in specs:
            active = 'resource/' + spec.resource_id in label
            code = ('STOPPED' if active else 'RUNNING') if spec.template == 'status' else ('HIGH' if active else 'NORMAL')
            evidence = {'resource_id': spec.resource_id, 'units': 'container_state', 'sample_count': 1,
                        'state': 'exited' if active else 'running'} if spec.template == 'status' else {
                        'resource_id': spec.resource_id, 'units': 'quota_fraction', 'sample_count': 60,
                        'median': 1. if active else .1, 'p95': 1. if active else .1,
                        'high_fraction': 1. if active else 0., 'coverage': 1., 'quota_cores': .25}
            packets.append(ToolResultV2(spec.action_id, spec.resource_id, 'OK', code,
                incident.window_start, incident.window_end, incident.window_end, 'synthetic', 0,
                collected_at=incident.window_end, encoding_hash=encoder,
                evidence_json=canonical(evidence)))
        records.append({'split': 'train', 'incident_id': f'fixture-{index}', 'label': label,
                        'packets': {p.action_id: p for p in packets}})
    model = fit_set_model(records, labels, specs, smoothing=0.)
    env = InvestigationEnv(topology, specs, model, ReplayBackend(tuple(records[row]['packets'].values()), specs))
    env.reset(incident)
    return env, labels[row]


def validate_multicause_contracts():
    env, truth = pair_environment()
    try:
        planner = BoundedDP(env.model, env.specs)
        result = investigate(env, planner, digest('fixture_dp'))
        score = env.probe_return + score_decision(env.decision, truth)
        result['explanation'] = explain_result(result)
        return {'passed': env.decision.cause_ids == PAIR and abs(score - .9) < 1e-12,
                'synthetic_only': True, 'return': score, 'result': result}
    finally:
        env.close()


def decision_definitions():
    from .diagnosis_sets import HYPOTHESES
    return {
        'singleton': {'labels': ((), (PAIR[0],)), 'responses': (('RUNNING',), ('STOPPED',)),
                      'resources': ('catalogue-db',), 'prices': (1,), 'depth1': .95, 'depth2': .95},
        'complementary': {'labels': ((), (PAIR[0],), (PAIR[1],), PAIR),
            'responses': (('RUNNING', 'NORMAL'), ('STOPPED', 'NORMAL'), ('RUNNING', 'HIGH'), ('STOPPED', 'HIGH')),
            'resources': ('catalogue-db', 'payment'), 'templates': ('status', 'cpu'), 'prices': (5, 5), 'depth1': .25, 'depth2': .5},
        'masked_second_root': {'labels': ((), (PAIR[0],), PAIR),
            'responses': (('RUNNING', 'NORMAL'), ('STOPPED', 'NORMAL'), ('STOPPED', 'HIGH')),
            'resources': ('catalogue-db', 'payment'), 'templates': ('status', 'cpu'), 'prices': (1, 2), 'depth1': 37/60, 'depth2': 53/60},
        'healthy_high_load': {'labels': ((), (PAIR[1],), PAIR),
            'responses': (('NORMAL', 'HIGH'), ('SLOW', 'HIGH'), ('SLOW', 'HIGH')),
            'resources': ('front-end', 'payment'), 'templates': ('latency', 'cpu'), 'prices': (1, 2), 'depth1': 37/60, 'depth2': 37/60},
        'ambiguous': {'labels': ((), (PAIR[0],), (PAIR[1],), PAIR, ('resource/carts',)), 'responses': (('HIGH',),)*5,
            'resources': ('payment',), 'templates': ('cpu',), 'prices': (1,), 'depth1': .25, 'depth2': .25},
        'insufficient_budget': {'labels': ((), (PAIR[0],), (PAIR[1],), PAIR),
            'responses': (('RUNNING', 'NORMAL'), ('STOPPED', 'NORMAL'), ('RUNNING', 'HIGH'), ('STOPPED', 'HIGH')),
            'resources': ('catalogue-db', 'payment'), 'templates': ('status', 'cpu'), 'prices': (5, 5), 'budget': 5, 'depth1': .25, 'depth2': .25},
        'unavailable_purchase': {'labels': ((), (PAIR[0],)), 'responses': (('TIMEOUT',), ('TIMEOUT',)),
            'resources': ('catalogue-db',), 'prices': (2,), 'depth1': .5, 'depth2': .5},
        'terminal_only': {'labels': ((), (PAIR[0],)), 'responses': (('RUNNING',), ('STOPPED',)),
            'resources': ('catalogue-db',), 'prices': (2,), 'budget': 1, 'depth1': .5, 'depth2': .5},
    }


def set_fixture_environment(definition, row=0):
    """Designed likelihood rows, independent of physical captures."""
    topology = sockshop_topology()
    encoder = digest({'synthetic_set_fixture': definition})
    specs = []
    for resource, price, template in zip(definition['resources'], definition['prices'],
                                        definition.get('templates', ('status',)*len(definition['resources']))):
        params = {} if template == 'status' else {'window_seconds': 120}
        if template == 'latency': params.update(operation='checkout', min_samples=20)
        specs.append(ToolSpecV2(f'{template}/{resource}/v2', resource, template, 2, canonical(params), price,
                               (template,), VOCABULARIES[template], encoder))
    specs = tuple(specs)
    incident = IncidentPublicV3('opaque-decision-fixture', topology.hash, catalogue_hash(specs), 'front-end',
        'manual_frontend', '2026-10-14T10:00:00Z', '2026-10-14T10:02:00Z', definition.get('budget', 10),
        definition.get('horizon', 5), .05, encoding_hash=encoder,
        objective_hash=digest(ObjectiveV3().to_dict()))
    records = []
    for index, label in enumerate(definition['labels']):
        packets = []
        for spec, response in zip(specs, definition['responses'][index]):
            unavailable = response == 'TIMEOUT'
            evidence = {'resource_id': spec.resource_id, 'units': 'container_state', 'sample_count': 1,
                        'state': 'running' if response == 'RUNNING' else 'exited'}
            if spec.template == 'cpu':
                evidence = {'resource_id': spec.resource_id, 'units': 'quota_fraction', 'sample_count': 60,
                            'median': 1. if response == 'HIGH' else .1, 'p95': 1. if response == 'HIGH' else .1,
                            'high_fraction': float(response == 'HIGH'), 'coverage': 1., 'quota_cores': .25}
            elif spec.template == 'latency':
                evidence = {'resource_id': spec.resource_id, 'units': 'milliseconds', 'sample_count': 60,
                            'operation': 'checkout', 'median_ms': 800. if response == 'SLOW' else 100.,
                            'p95_ms': 800. if response == 'SLOW' else 100., 'coverage': 1., 'timeout_count': 0, 'failed_count': 0}
            packets.append(ToolResultV2(spec.action_id, spec.resource_id, 'TIMEOUT' if unavailable else 'OK',
                'UNAVAILABLE' if unavailable else response, incident.window_start, incident.window_end, incident.window_end,
                'abstract_fixture', 0, collected_at=incident.window_end, encoding_hash=encoder,
                reason='synthetic_timeout' if unavailable else '', evidence_json='{}' if unavailable else canonical(evidence)))
        records.append({'split': 'train', 'incident_id': f'abstract-{index}', 'label': label,
                        'packets': {p.action_id: p for p in packets}})
    labels = tuple(dict.fromkeys(definition['labels']))
    model = fit_set_model(records, labels, specs, smoothing=0.)
    env = InvestigationEnv(topology, specs, model, ReplayBackend(tuple(records[row]['packets'].values()), specs))
    env.reset(incident)
    return env, definition['labels'][row]


def validate_decision_fixtures():
    checks = {}
    for name, definition in decision_definitions().items():
        env, truth = set_fixture_environment(definition)
        try:
            planners = [BoundedDP(env.model, env.specs, depth=d) for d in (1, 2)]
            actions = [planner.choose(env.observation()) for planner in planners]
            values = [max(planner.stats['values'].values()) for planner in planners]
            passed = all(abs(values[d-1]-definition[f'depth{d}']) < 1e-10 for d in (1, 2))
            if name == 'complementary': passed &= actions[0] == 'DEFER' and actions[1] not in ('STOP', 'DEFER')
            if name == 'ambiguous': passed &= actions == ['DEFER', 'DEFER']
            if name == 'unavailable_purchase':
                env.step(env.specs[0].action_id)
                passed &= env.ledger.credits == 2 and env.probe_return == -.1 and env.terminated
            checks[name] = {'passed': bool(passed), 'actions': actions, 'values': values}
        finally:
            env.close()
    return {'passed': all(row['passed'] for row in checks.values()), 'synthetic_only': True, 'checks': checks}
