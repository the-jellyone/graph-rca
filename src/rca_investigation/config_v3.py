"""Explicit set-task registration with independent v3 artifact paths."""
from pathlib import Path
from .contracts import canonical, integer, real, unique
from .contracts_v2 import decode_record, sha256
from .contracts_v3 import ObjectiveV3
from .diagnosis_sets import PROFILES, cause_set
from .config import TaskConfig, keys


def load_v3_config(value, topology):
    keys(value, {'schema_version', 'task_id', 'resources', 'diagnoses', 'entry_protocol', 'actions',
                 'episode', 'objective', 'model', 'planner', 'training', 'encoding_hash', 'paths'})
    task = value['task_id']
    if type(value['schema_version']) is not int or value['schema_version'] != 3 or task not in PROFILES:
        raise ValueError('unregistered v3 task')
    if value['entry_protocol'] != 'manual_frontend' or value['diagnoses'] != [list(v) for v in PROFILES[task]]:
        raise ValueError('v3 hypotheses or entry mismatch')
    required = {'front-end', 'catalogue', 'catalogue-db', 'orders', 'payment', 'carts', 'carts-db', 'orders-db', 'user', 'shipping'}
    unique(tuple(value['resources']))
    if set(value['resources']) != required or not required <= set(topology.resources):
        raise ValueError('v3 resource scope mismatch')
    sha256(value['encoding_hash'])
    specs = tuple(decode_record(action, 'tool') for action in value['actions'])
    if not specs or len({s.action_id for s in specs}) != len(specs):
        raise ValueError('v3 requires unique declared tools')
    for spec in specs:
        spec.validate_resource(topology)
        if spec.resource_id not in required or spec.encoding_hash != value['encoding_hash']:
            raise ValueError('v3 action context mismatch')
    if {s.resource_id for s in specs if s.template == 'status'} != required:
        raise ValueError('v3 requires status for every registered resource')
    keys(value['episode'], {'budget', 'max_probes', 'observation_window_seconds'})
    for number in value['episode'].values():
        integer(number, 1)
    if value['episode'] != {'budget': 10, 'max_probes': 5, 'observation_window_seconds': 120}:
        raise ValueError('new budget requires a separately registered task')
    keys(value['objective'], {'lambda_cost', 'correct', 'incorrect', 'defer'})
    ObjectiveV3(**value['objective'])
    if value['objective'] != {'lambda_cost': .05, 'correct': 1., 'incorrect': 0., 'defer': .25}:
        raise ValueError('objective differs from primary declaration')
    keys(value['model'], {'prior', 'smoothing'})
    real(value['model']['smoothing'], 0, 1)
    if value['model']['prior'] != 'balanced_hypotheses' or value['model']['smoothing'] == 0:
        raise ValueError('production set model requires balanced prior and smoothing')
    keys(value['planner'], {'depth', 'max_expanded_states'})
    if value['planner'] != {'depth': 2, 'max_expanded_states': 50000}:
        raise ValueError('planner differs from primary declaration')
    keys(value['training'], {'algorithm', 'episodes', 'alpha', 'gamma', 'epsilon_start', 'epsilon_end',
                            'epsilon_decay_episodes', 'validation_interval', 'seeds'})
    t = value['training']
    if t['algorithm'] != 'expected_sarsa' or t['gamma'] != 1:
        raise ValueError('unsupported set training settings')
    for key in ('episodes', 'validation_interval', 'epsilon_decay_episodes'):
        integer(t[key], 1)
    for key in ('alpha', 'epsilon_start', 'epsilon_end'):
        real(t[key], 0, 1)
    if t['alpha'] == 0 or t['epsilon_start'] < t['epsilon_end']:
        raise ValueError('invalid learning or exploration rate')
    if type(t['seeds']) is not list or t['seeds'] != [11, 22, 33, 44, 55]:
        raise ValueError('v3 requires the declared five learner seeds')
    keys(value['paths'], {'dataset', 'raw_dataset', 'encoder', 'model', 'runs', 'resource_registry'})
    if len(set(value['paths'].values())) != len(value['paths']):
        raise ValueError('v3 paths must differ')
    for path in value['paths'].values():
        if type(path) is not str or not path or Path(path).is_absolute() or '..' in Path(path).parts or '\\' in path or ':' in path or 'v3' not in path:
            raise ValueError('v3 artifacts require distinct relative v3 paths')
    return TaskConfig(canonical(value))
