"""Vanilla masked DQN inference. Torch is optional until a neural command runs."""
import numpy as np
from ..contracts import digest


def network(dimension, actions, hidden=(128, 128)):
    import torch.nn as nn
    return nn.Sequential(nn.Linear(dimension, hidden[0]), nn.ReLU(), nn.Linear(hidden[0], hidden[1]),
                         nn.ReLU(), nn.Linear(hidden[1], actions))


class DQNPolicy:
    def __init__(self, schema, *, seed=11, hidden=(128, 128)):
        import torch
        torch.set_num_threads(1)
        torch.use_deterministic_algorithms(True)
        # Initializing a new policy must not consume another trainer's RNG.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            self.online = network(schema.dimension, len(schema.actions), hidden)
        self.online.eval()
        self.schema, self.hidden = schema, tuple(hidden)
        self.rng = np.random.default_rng(seed)
        self.stats = {}

    def choose(self, observation, epsilon=0.):
        import torch
        if not 0 <= epsilon <= 1:
            raise ValueError('invalid epsilon')
        state = self.schema.encode(observation)
        mask = self.schema.mask(observation)
        legal = np.flatnonzero(mask)
        if not len(legal):
            raise ValueError('no legal action in a terminated incident')
        if epsilon > 0 and self.rng.random() < epsilon:
            index = int(self.rng.choice(legal))
        else:
            with torch.no_grad():
                values = self.online(torch.from_numpy(state)).numpy()
            if not np.isfinite(values).all():
                raise ValueError('nonfinite action values')
            index = int(legal[np.argmax(values[legal])])
        self.stats = {'fallback': False, 'neural': True}
        return self.schema.actions[index]

    @property
    def hash(self):
        return digest({'schema_hash': self.schema.hash, 'hidden': self.hidden,
                       'weights': {key: value.detach().cpu().tolist() for key, value in self.online.state_dict().items()}})
