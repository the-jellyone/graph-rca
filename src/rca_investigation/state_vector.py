"""Versioned numeric state built exclusively from purchased public evidence."""
from dataclasses import dataclass
import numpy as np
from .contracts import canonical, digest
from .belief import vocabulary
from .catalogue import catalogue_hash


@dataclass(frozen=True)
class StateVectorSchema:
    canonical_json: str

    @property
    def values(self): return __import__('json').loads(self.canonical_json)
    @property
    def hash(self): return digest(self.values)
    @property
    def actions(self): return tuple(self.values['actions'])
    @property
    def dimension(self): return len(self.values['features'])

    @classmethod
    def build(cls, specs, model, *, budget=10, probes=5, objective=None):
        if budget <= 0 or probes <= 0:
            raise ValueError('positive state scales required')
        objective = objective or {'lambda_cost': .05, 'correct': 1., 'incorrect': 0., 'defer': .25}
        features, vocabularies = [], {}
        for spec in specs:
            vocab = vocabulary(spec)
            vocabularies[spec.action_id] = list(vocab)
            features += [f'unbought:{spec.action_id}'] + [f'evidence:{spec.action_id}:{symbol}' for symbol in vocab]
        actions = [s.action_id for s in specs] + ['STOP', 'DEFER']
        features += [f'legal:{a}' for a in actions] + [f'price:{s.action_id}' for s in specs]
        features += ['remaining_credits', 'remaining_probes', 'lambda_cost', 'correct', 'incorrect', 'defer']
        features += [f'posterior:{c}' for c in model.classes]
        return cls(canonical({'schema_version': 3, 'feature_version': 'public_evidence_vector_v2',
            'actions': actions, 'vocabularies': vocabularies, 'features': features, 'classes': model.classes,
            'model_hash': model.hash, 'catalogue_hash': catalogue_hash(specs),
            'prices': {s.action_id: s.credits for s in specs},
            'scales': {'credits': budget, 'probes': probes, 'price': max(budget, max(s.credits for s in specs))},
            'objective': objective}))

    def encode(self, observation):
        value = self.values
        if observation.get('schema_version') != 3 or observation['catalogue_hash'] != value['catalogue_hash'] or observation.get('model_hash') != value['model_hash']:
            raise ValueError('observation schema/catalogue mismatch')
        if observation['prices'] != value['prices'] or observation['lambda_cost'] != value['objective']['lambda_cost'] or observation['objective'] != {k: v for k, v in value['objective'].items() if k != 'lambda_cost'}:
            raise ValueError('public prices/objective differ from feature schema')
        history = dict(observation['history'])
        if len(history) != len(observation['history']) or set(history) - set(value['vocabularies']):
            raise ValueError('duplicate or unknown purchased action')
        if set(observation['mask']) - set(value['actions']) or set(observation['diagnoses']) != set(value['classes']):
            raise ValueError('unknown legal action or hypothesis')
        features = []
        for action, vocab in value['vocabularies'].items():
            if action in history and history[action] not in vocab:
                raise ValueError('unknown purchased response')
        # Iterate the declared action order, never the dictionary's sorted order.
        for action in value['actions'][:-2]:
            features.append(float(action not in history))
            features += [float(history.get(action) == symbol) for symbol in value['vocabularies'][action]]
        features += [float(action in observation['mask']) for action in value['actions']]
        features += [value['prices'][action]/value['scales']['price'] for action in value['actions'][:-2]]
        features += [observation['remaining_credits']/value['scales']['credits'],
                     observation['remaining_probes']/value['scales']['probes'], observation['lambda_cost']]
        features += [observation['objective'][key] for key in ('correct', 'incorrect', 'defer')]
        probabilities = [observation['diagnoses'][key] for key in value['classes']]
        if not np.isclose(sum(probabilities), 1):
            raise ValueError('posterior is not normalized')
        features += probabilities
        result = np.asarray(features, dtype=np.float32)
        if result.shape != (self.dimension,) or not np.isfinite(result).all() or np.any(result < 0) or np.any(result > 1):
            raise ValueError('feature values outside declared bounds')
        return result

    def mask(self, observation):
        if set(observation['mask']) - set(self.actions):
            raise ValueError('unknown masked action')
        return np.asarray([a in observation['mask'] for a in self.actions], dtype=bool)
