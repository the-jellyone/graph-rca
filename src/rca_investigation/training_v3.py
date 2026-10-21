"""Shared task gate and sampler for all v3 learners."""
from pathlib import Path
import hashlib
import json
from .dataset import read_split, load_public_bundle, load_private_label
from .diagnosis_sets import hypothesis_key
from .experiment import label_name
from .contracts import digest


def require_diagnostic_gate(config, model, split_hash):
    path = Path(config.values['paths']['runs']) / 'full-evidence-reference.json'
    reference = json.loads(path.read_text())
    if (reference['config_hash'], reference['model_hash'], reference['split_hash']) != (config.hash, model.hash, split_hash):
        raise ValueError('v3 diagnostic gate belongs to another task/model/split')
    if not reference['training_promotion_passed']:
        raise ValueError('v3 per-stratum diagnostic gate failed; policy training is blocked')
    return reference


def balanced_groups(paths, classes):
    result = {key: {} for key in classes}
    for path in sorted(paths):
        label = hypothesis_key(label_name(load_private_label(path)))
        receipt = json.loads((path / 'capture_receipt.json').read_text())
        if receipt['role'] != 'train': raise ValueError('sampler only accepts training incidents')
        result[label].setdefault(receipt['stratum'], []).append(path.name)
    if any(not strata for strata in result.values()):
        raise ValueError('sampler requires complete diagnosis-set coverage')
    return {key: {stratum: tuple(ids) for stratum, ids in sorted(strata.items())} for key, strata in result.items()}


def train_neural_dataset(dataset, specs, model, config, destination, *, seed=11, resume=False, stop_after=None):
    from .backends.replay import ReplayBackend
    from .environment import InvestigationEnv
    from .state_vector import StateVectorSchema
    from .train_dqn import train_loop
    if config.values['schema_version'] != 3 or config.values['task_id'] != 'sockshop_multicause_v3':
        raise ValueError('primary DQN is registered on the multi-cause task only')
    manifest, paths = read_split(dataset, 'train')
    _, validation = read_split(dataset, 'validation')
    require_diagnostic_gate(config, model, manifest['hash'])
    groups = balanced_groups(paths, model.classes)
    by_id = {p.name: (load_public_bundle(p, specs), label_name(load_private_label(p))) for p in paths}
    objective = config.values['objective']
    schema = StateVectorSchema.build(specs, model, budget=config.values['episode']['budget'],
                                    probes=config.values['episode']['max_probes'], objective=objective)
    def factory(identifier):
        (public, topology, packets), label = by_id[identifier]
        env = InvestigationEnv(topology, specs, model, ReplayBackend(packets, specs), defer_utility=objective['defer'],
                               correct_utility=objective['correct'], incorrect_utility=objective['incorrect'])
        env.reset(public)
        return env, label
    def validate(policy):
        from .evaluate_decisions import evaluate_paths
        return evaluate_paths(validation, specs, model, config, policy, policy.hash)[0]
    lock = Path('experiments/v3/neural/uv.lock')
    return train_loop(factory, tuple(by_id), validate, schema,
        {'model_hash': model.hash, 'task_hash': config.hash, 'split_hash': manifest['hash'],
         'neural_lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
         'core_lock_sha256': hashlib.sha256(Path('uv.lock').read_bytes()).hexdigest()},
        config.values['training'], destination, seed=seed, resume=resume, stop_after=stop_after, sampler_groups=groups)
