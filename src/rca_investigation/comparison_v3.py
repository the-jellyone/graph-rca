"""Pre-test method sealing and one resumable paired v3 comparison per task."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import numpy as np
from .contracts import digest
from .dataset import atomic_json, read_split
from .evaluate_decisions import evaluate_paths, paired_session_interval
from .train import runtime_hash
from .training_v3 import require_diagnostic_gate
from .policies.dp import BoundedDP
from .policies.rules import RulePolicy, FixedSequencePolicy, FrozenRuleTransfer
from .policies.rules_v3 import ExpandedSetRule
from .policies.expected_sarsa import TabularPolicy, HybridPolicy


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def policy_identity(definition, model, config):
    return digest({'definition': definition, 'model_hash': model.hash, 'task_hash': config.hash,
                   'runtime_hash': runtime_hash(), 'checkpoint_sha256': file_hash(definition['checkpoint']) if 'checkpoint' in definition else None})


def policy_from_definition(definition, model, specs, config):
    kind, objective = definition['kind'], config.values['objective']
    if kind in ('dp', 'voi'):
        return BoundedDP(model, specs, depth=definition['depth'], max_expanded_states=50000,
                         correct=objective['correct'], incorrect=objective['incorrect'], defer=objective['defer'])
    if kind == 'fixed-sequence': return FixedSequencePolicy(definition['sequence'], definition['confidence'])
    if kind == 'expanded-rule': return ExpandedSetRule(specs)
    if kind == 'frozen-rule-transfer': return FrozenRuleTransfer(objective=objective)
    if kind == 'rule': return RulePolicy(definition['name'], definition.get('seed', 11), objective)
    path = Path(definition['checkpoint'])
    if kind == 'dqn':
        import torch
        from .state_vector import StateVectorSchema
        from .policies.dqn import DQNPolicy
        checkpoint = torch.load(path, map_location='cpu', weights_only=False)
        schema = StateVectorSchema(checkpoint['feature_schema'])
        expected = StateVectorSchema.build(specs, model, budget=config.values['episode']['budget'],
            probes=config.values['episode']['max_probes'], objective=objective)
        if schema.hash != expected.hash or checkpoint['identity']['task_hash'] != config.hash or checkpoint['identity']['model_hash'] != model.hash:
            raise ValueError('DQN checkpoint model/task/schema mismatch')
        if checkpoint['identity']['runtime_hash'] != runtime_hash() or checkpoint['identity']['neural_lock_sha256'] != file_hash('experiments/v3/neural/uv.lock'):
            raise ValueError('DQN checkpoint source/neural lock changed')
        policy = DQNPolicy(schema, seed=definition['seed'], hidden=checkpoint['identity']['settings']['hidden'])
        policy.online.load_state_dict(checkpoint['online'])
        if policy.hash != checkpoint['policy_hash']: raise ValueError('DQN weights checksum mismatch')
        return policy
    if kind not in ('tabular', 'hybrid'): raise ValueError('unregistered v3 method')
    checkpoint = json.loads(path.read_text())
    policy = TabularPolicy.restore(checkpoint['policy'])
    if policy.hash != checkpoint['policy_hash'] or policy.model_hash != model.hash or policy.task_hash != config.hash or checkpoint['identity']['runtime_hash'] != runtime_hash():
        raise ValueError('tabular checkpoint source/model/task mismatch')
    return HybridPolicy(policy, BoundedDP(model, specs, depth=2, max_expanded_states=50000,
        correct=objective['correct'], incorrect=objective['incorrect'], defer=objective['defer']), min_visits=5) if kind == 'hybrid' else policy


def environment_identity():
    import torch
    expected = Path('experiments/v3/neural/.venv/Scripts/python.exe').resolve()
    if Path(sys.executable).resolve() != expected:
        raise ValueError('all primary v3 comparisons must run in the same pinned CPU neural environment')
    torch.set_num_threads(1)
    return {'python': sys.version, 'torch': torch.__version__, 'numpy': np.__version__, 'torch_threads': 1,
            'neural_lock_sha256': file_hash('experiments/v3/neural/uv.lock'), 'core_lock_sha256': file_hash('uv.lock')}


def definitions(model, specs, config, training_root):
    result = {'stop-immediately': {'kind': 'rule', 'name': 'stop-immediately'},
              'defer-immediately': {'kind': 'rule', 'name': 'defer-immediately'},
              'frozen-old-rule': {'kind': 'frozen-rule-transfer'}, 'expanded-rule': {'kind': 'expanded-rule'},
              'voi': {'kind': 'voi', 'depth': 1}, 'dp': {'kind': 'dp', 'depth': 2}}
    for seed in config.values['training']['seeds']:
        result[f'random-valid-{seed}'] = {'kind': 'rule', 'name': 'random-valid', 'seed': seed}
        for method in ('expected-sarsa', 'two-step-sarsa'):
            root = Path(training_root)/method/str(seed)
            latest = json.loads((root/'latest.json').read_text())
            if latest['episode'] != 10000: raise ValueError('all declared tabular runs must complete before sealing')
            for kind in ('tabular', 'hybrid'):
                result[f'{method}-{seed}-{kind}'] = {'kind': kind, 'method': method, 'seed': seed, 'checkpoint': str((root/'best.json').resolve())}
        if config.values['task_id'] == 'sockshop_multicause_v3':
            import torch
            root = Path(training_root)/'dqn'/str(seed)
            latest = torch.load(root/'latest.pt', map_location='cpu', weights_only=False)
            if latest['episodes'] != 10000: raise ValueError('all five DQN runs must finish before sealing')
            result[f'dqn-{seed}'] = {'kind': 'dqn', 'method': 'dqn', 'seed': seed, 'variant': 'vanilla', 'checkpoint': str((root/'best.pt').resolve())}
    return result


def evaluate(paths, specs, model, config, definition):
    policy = policy_from_definition(definition, model, specs, config)
    # Warm-up uses public state only and cannot alter this method's episode RNG.
    from .experiment import replay_environment
    env = replay_environment(paths[0], specs, model, config)
    warm = deepcopy(policy)
    try:
        for _ in range(25 if definition['kind'] == 'dqn' else 1): warm.choose(env.observation())
    finally: env.close()
    return evaluate_paths(paths, specs, model, config, policy, policy_identity(definition, model, config))


def compare_validation(dataset, specs, model, config, training_root, destination, frozen_path):
    if Path(frozen_path).exists(): raise ValueError('v3 methods are already sealed')
    environment = environment_identity()
    manifest, paths = read_split(dataset, 'validation')
    reference = require_diagnostic_gate(config, model, manifest['hash'])
    defs = definitions(model, specs, config, training_root)
    identity = {'config_hash': config.hash, 'model_hash': model.hash, 'split_hash': manifest['hash'],
                'runtime_hash': runtime_hash(), 'environment': environment,
                'checkpoints': {d['checkpoint']: file_hash(d['checkpoint']) for d in defs.values() if 'checkpoint' in d}}
    partial_path = Path(destination).with_suffix('.partial.json')
    partial = {'identity': identity, 'candidates': {}, 'summaries': {}, 'outcomes': {}}
    if partial_path.exists():
        partial = json.loads(partial_path.read_text())
        if partial['identity'] != identity: raise ValueError('partial v3 validation identity changed')
    from .adaptive_comparison import fixed_candidates
    candidate_results = {}
    for index, sequence in enumerate(fixed_candidates(specs, maximum=32, model=model)):
        for confidence in (.6, .75, .9, .95, .99):
            name = f'fixed-{index}-{confidence}'
            definition = {'kind': 'fixed-sequence', 'sequence': list(sequence), 'confidence': confidence}
            if name not in partial['candidates']:
                summary, _ = evaluate(paths, specs, model, config, definition)
                partial['candidates'][name] = {'summary': summary, 'definition': definition}
                atomic_json(partial_path, partial)
            candidate_results[name] = partial['candidates'][name]
    selected_fixed = max(candidate_results, key=lambda n: (candidate_results[n]['summary']['mean_return'], -candidate_results[n]['summary']['mean_credits']))
    defs[selected_fixed] = candidate_results[selected_fixed]['definition']
    summaries, outcomes = partial['summaries'], partial['outcomes']
    for name, definition in defs.items():
        if name in summaries: continue
        summaries[name], outcomes[name] = evaluate(paths, specs, model, config, definition)
        atomic_json(partial_path, partial)
    baseline_names = ['voi', 'dp', 'expanded-rule', 'frozen-old-rule', 'stop-immediately', 'defer-immediately', selected_fixed]
    baseline_names += [name for name in defs if name.startswith('random-valid-')]
    strongest = max(baseline_names, key=lambda n: (summaries[n]['mean_return'], -summaries[n]['mean_credits']))
    representatives = {}
    for method, suffix in [('expected-sarsa', '-tabular'), ('two-step-sarsa', '-tabular'),
                           ('expected-sarsa', '-hybrid'), ('two-step-sarsa', '-hybrid'), ('dqn', '')]:
        names = [n for n in defs if n.startswith(method+'-') and (not suffix or n.endswith(suffix))]
        if names: representatives[method + ('-hybrid' if suffix == '-hybrid' else '')] = max(names, key=lambda n: (summaries[n]['mean_return'], -summaries[n]['mean_credits']))
    selected = max(defs, key=lambda n: (summaries[n]['mean_return'], -summaries[n]['mean_credits']))
    report = {'passed': True, 'split': 'validation', 'selected': selected, 'summaries': summaries, 'outcomes': outcomes,
              'fixed_search': candidate_results, 'selected_fixed': selected_fixed, 'strongest_nonlearning': strongest,
              'representatives': representatives, 'environment': environment}
    frozen = {'schema_version': 3, 'task_id': config.values['task_id'], 'dataset': str(Path(dataset).resolve()), 'config_hash': config.hash,
              'robustness_sha256': file_hash('experiments/v3/robustness-declaration.json'),
              'model_hash': model.hash, 'split_hash': manifest['hash'], 'runtime_hash': runtime_hash(),
              'definitions': defs, 'selected': selected, 'selected_fixed': selected_fixed, 'strongest_nonlearning': strongest,
              'representatives': representatives, 'environment': environment, 'diagnostic_gate_passed': reference['training_promotion_passed'],
              'checkpoint_hashes': {d['checkpoint']: file_hash(d['checkpoint']) for d in defs.values() if 'checkpoint' in d}}
    frozen['hash'] = digest(frozen)
    atomic_json(destination, report)
    atomic_json(frozen_path, frozen)
    return report


def load_frozen(path, dataset, config, model):
    frozen = json.loads(Path(path).read_text())
    claimed = frozen.pop('hash')
    if digest(frozen) != claimed or frozen['schema_version'] != 3 or frozen['task_id'] != config.values['task_id'] or frozen['config_hash'] != config.hash or frozen['model_hash'] != model.hash:
        raise ValueError('v3 sealed method/task checksum mismatch')
    if frozen['runtime_hash'] != runtime_hash() or frozen['dataset'] != str(Path(dataset).resolve()) or frozen['environment'] != environment_identity():
        raise ValueError('v3 source/dataset/CPU environment changed after sealing')
    if frozen['robustness_sha256'] != file_hash('experiments/v3/robustness-declaration.json'):
        raise ValueError('v3 robustness declaration changed after method sealing')
    for checkpoint, expected in frozen['checkpoint_hashes'].items():
        if file_hash(checkpoint) != expected: raise ValueError('v3 sealed checkpoint changed')
    frozen['hash'] = claimed
    return frozen


def seal_both_comparisons(single_path, multi_path, destination):
    manifests = [json.loads(Path(path).read_text()) for path in (single_path, multi_path)]
    if [m.get('task_id') for m in manifests] != ['sockshop_single_v3', 'sockshop_multicause_v3']:
        raise ValueError('seal the distinct single and multi scopes in declared order')
    if any(m.get('robustness_sha256') != file_hash('experiments/v3/robustness-declaration.json') for m in manifests):
        raise ValueError('robustness matrix must be frozen before reserved comparisons')
    if any(m['runtime_hash'] != runtime_hash() or m['hash'] != digest({k: v for k, v in m.items() if k != 'hash'}) for m in manifests):
        raise ValueError('both scope manifests must be valid before any reserved comparison')
    value = {'schema_version': 3, 'single': manifests[0]['hash'], 'multi': manifests[1]['hash'],
             'runtime_hash': runtime_hash(), 'overlapping_test_incidents': True}
    target = Path(destination)
    if target.exists() and json.loads(target.read_text()) != value: raise ValueError('joint reserved seal changed')
    atomic_json(target, value)
    return value


def compare_test(dataset, specs, model, config, frozen_path, destination):
    frozen = load_frozen(frozen_path, dataset, config, model)
    joint = json.loads(Path('experiments/v3/comparisons-sealed.json').read_text())
    scope = 'multi' if config.values['task_id'] == 'sockshop_multicause_v3' else 'single'
    if joint['runtime_hash'] != runtime_hash() or frozen['hash'] != joint[scope]:
        raise ValueError('single and multi scopes must be jointly sealed before reserved evaluation')
    manifest, paths = read_split(dataset, 'test')
    if manifest['hash'] != frozen['split_hash']: raise ValueError('v3 reserved split changed')
    destination = Path(destination)
    report = {'passed': False, 'split': 'test', 'frozen_hash': frozen['hash'], 'selected': frozen['selected'],
              'summaries': {}, 'outcomes': {}, 'physical_incidents': len(paths)}
    if destination.exists():
        report = json.loads(destination.read_text())
        if report['passed'] or report['frozen_hash'] != frozen['hash']: raise ValueError('reserved comparison completed or identity changed')
    atomic_json(destination, report)
    for name, definition in frozen['definitions'].items():
        if name in report['summaries']: continue
        report['summaries'][name], report['outcomes'][name] = evaluate(paths, specs, model, config, definition)
        atomic_json(destination, report)
    baseline = frozen['strongest_nonlearning']
    report['paired_vs_strongest_nonlearning'] = {name: paired_session_interval(rows, report['outcomes'][baseline], seed=11, samples=10000)
        for name, rows in report['outcomes'].items() if name != baseline}
    report['paired_vs_fixed_sequence'] = {name: paired_session_interval(rows, report['outcomes'][frozen['selected_fixed']], seed=11, samples=10000)
        for name, rows in report['outcomes'].items() if name != frozen['selected_fixed']}
    report['seed_families'] = seed_families(frozen['definitions'], report['summaries'])
    report['test_capture_sessions'] = len({row['capture_session'] for rows in report['outcomes'].values() for row in rows})
    report['compute_saving_claim_evaluated'] = False
    report['promotion'] = {}
    for name, pair in report['paired_vs_strongest_nonlearning'].items():
        summary = report['summaries'][name]
        accuracy = summary['accuracy'] >= .9 and summary['healthy_specificity'] >= .9
        if config.values['task_id'] == 'sockshop_multicause_v3': accuracy &= summary['pair_accuracy'] >= .9
        report['promotion'][name] = {'passed': frozen['diagnostic_gate_passed'] and accuracy and
            pair['mean_paired_return_difference'] >= .02 and pair['session_bootstrap_95'][0] > 0,
            'baseline': baseline}
    report['all_seed_results_visible'] = True
    report['passed'] = True
    atomic_json(destination, report)
    return report


def seed_families(definitions, summaries):
    families = {}
    for name, definition in definitions.items():
        if 'method' not in definition: continue
        family = definition['method'] + '/' + definition['kind']
        families.setdefault(family, []).append(name)
    result = {}
    for family, names in families.items():
        fields = ('accuracy', 'pair_accuracy', 'healthy_specificity', 'mean_return', 'mean_credits',
                  'mean_probes', 'decision_latency_ms', 'decision_median_ms', 'decision_p95_ms')
        metrics = {}
        for field in fields:
            values = [summaries[name][field] for name in names if summaries[name].get(field) is not None]
            if values:
                metrics[field] = {'mean': float(np.mean(values)), 'sample_std': float(np.std(values, ddof=1)) if len(values)>1 else 0.,
                    'minimum': float(min(values)), 'maximum': float(max(values))}
        result[family] = {'seeds': [definitions[name]['seed'] for name in names], 'members': names,
                          'seed_count': len(names), 'metrics': metrics}
    return result
