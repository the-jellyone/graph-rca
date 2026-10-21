"""Training-only uniform replay, vanilla targets and complete CPU continuation."""
from copy import deepcopy
from pathlib import Path
import os
import random
import numpy as np
from .environment import score_decision
from .policies.dqn import DQNPolicy


DEFAULTS = {'hidden': (128, 128), 'learning_rate': .001, 'gamma': 1., 'capacity': 50000,
            'batch_size': 64, 'warmup': 1000, 'target_sync': 1000, 'gradient_cap': 10.}


class ReplayBuffer:
    def __init__(self, capacity=50000):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('positive replay capacity required')
        self.capacity, self.rows, self.cursor = capacity, [], 0

    def add(self, state, action, reward, next_state, next_mask, terminal, *, split='train'):
        if split != 'train':
            raise ValueError('DQN replay accepts training transitions only')
        state, next_state = np.asarray(state, dtype=np.float32), np.asarray(next_state, dtype=np.float32)
        mask = np.asarray(next_mask, dtype=bool)
        if state.ndim != 1 or next_state.shape != state.shape or mask.ndim != 1 or not np.isfinite(state).all() or not np.isfinite(next_state).all() or not np.isfinite(reward):
            raise ValueError('invalid replay transition')
        if type(action) is not int or not 0 <= action < len(mask) or type(terminal) is not bool or not terminal and not mask.any() or terminal and mask.any():
            raise ValueError('invalid replay action or terminal mask')
        row = (state.copy(), action, float(reward), next_state.copy(), mask.copy(), terminal)
        if len(self.rows) < self.capacity:
            self.rows.append(row)
        else:
            self.rows[self.cursor] = row
        self.cursor = (self.cursor+1) % self.capacity

    def sample(self, rng, batch_size):
        if batch_size > len(self.rows):
            raise ValueError('insufficient replay rows')
        return [self.rows[int(i)] for i in rng.choice(len(self.rows), batch_size, replace=False)]


def vanilla_targets(target, next_states, next_masks, rewards, terminals, gamma=1.):
    import torch
    if next_masks.dtype != torch.bool or terminals.dtype != torch.bool:
        raise ValueError('bootstrap requires boolean masks and terminal flags')
    result = rewards.clone()
    live = ~terminals
    if live.any():
        if not next_masks[live].any(dim=1).all():
            raise ValueError('nonterminal transition has no legal next action')
        with torch.no_grad():
            values = target(next_states[live]).masked_fill(~next_masks[live], -torch.inf)
            result[live] += gamma * values.max(dim=1).values
    if not torch.isfinite(result).all():
        raise ValueError('nonfinite Bellman target')
    return result.detach()


class DQNTrainer:
    def __init__(self, schema, identity, *, seed=11, settings=None):
        import torch
        self.settings = {**DEFAULTS, **(settings or {})}
        if self.settings['gamma'] != 1 or min(self.settings[k] for k in ('capacity', 'batch_size', 'warmup', 'target_sync')) < 1:
            raise ValueError('unsupported DQN training settings')
        self.policy = DQNPolicy(schema, seed=seed, hidden=self.settings['hidden'])
        self.target = deepcopy(self.policy.online)
        self.target.requires_grad_(False)
        self.target.eval()
        self.optimizer = torch.optim.Adam(self.policy.online.parameters(), lr=self.settings['learning_rate'])
        self.buffer = ReplayBuffer(self.settings['capacity'])
        self.sampler = np.random.default_rng(seed+100000)
        self.replay_rng = np.random.default_rng(seed+200000)
        self.identity = {**identity, 'schema_hash': schema.hash, 'torch_version': torch.__version__,
                         'settings': self.settings, 'seed': seed, 'variant': 'vanilla_dqn'}
        self.episodes = self.transitions = self.gradient_updates = 0
        self.metrics, self.selection = [], None
        self.last_update = None
        self.resources = None
        self.action_counts, self.incident_counts = {}, {}

    def update(self):
        import torch
        if len(self.buffer.rows) < max(self.settings['warmup'], self.settings['batch_size']):
            return None
        rows = self.buffer.sample(self.replay_rng, self.settings['batch_size'])
        states = torch.from_numpy(np.stack([r[0] for r in rows]))
        actions = torch.tensor([r[1] for r in rows], dtype=torch.long)
        rewards = torch.tensor([r[2] for r in rows], dtype=torch.float32)
        next_states = torch.from_numpy(np.stack([r[3] for r in rows]))
        masks = torch.from_numpy(np.stack([r[4] for r in rows]))
        terminals = torch.tensor([r[5] for r in rows], dtype=torch.bool)
        targets = vanilla_targets(self.target, next_states, masks, rewards, terminals, self.settings['gamma'])
        predictions = self.policy.online(states).gather(1, actions[:, None]).squeeze(1)
        loss = torch.nn.functional.smooth_l1_loss(predictions, targets)
        if not torch.isfinite(loss):
            raise ValueError('nonfinite DQN loss')
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(self.policy.online.parameters(), self.settings['gradient_cap'], error_if_nonfinite=True)
        self.optimizer.step()
        self.gradient_updates += 1
        if self.gradient_updates % self.settings['target_sync'] == 0:
            self.target.load_state_dict(self.policy.online.state_dict())
        self.last_update = {'loss': float(loss.detach()), 'gradient_norm': float(norm), 'gradient_updates': self.gradient_updates}
        return self.last_update

    def episode(self, env, label, epsilon):
        trace = []
        while not env.terminated:
            observation = env.observation()
            action = self.policy.choose(observation, epsilon)
            state = self.policy.schema.encode(observation)
            next_observation, reward, terminal = env.step(action, policy_hash=self.policy.hash)
            if terminal:
                reward += score_decision(env.decision, label, correct=env.correct_utility,
                                         incorrect=env.incorrect_utility, defer=env.defer_utility)
            self.buffer.add(state, self.policy.schema.actions.index(action), reward,
                self.policy.schema.encode(next_observation), self.policy.schema.mask(next_observation), terminal)
            self.transitions += 1
            self.update()
            trace.append({'action': action, 'reward': reward, 'terminal': terminal})
        self.episodes += 1
        return {'return': sum(row['reward'] for row in trace), 'credits': env.ledger.credits, 'trace': trace}

    def state(self):
        import torch
        return {'identity': self.identity, 'feature_schema': self.policy.schema.canonical_json,
            'online': self.policy.online.state_dict(), 'target': self.target.state_dict(),
            'optimizer': self.optimizer.state_dict(), 'replay': self.buffer.rows, 'cursor': self.buffer.cursor,
            'sampler': self.sampler.bit_generator.state, 'replay_rng': self.replay_rng.bit_generator.state,
            'policy_rng': self.policy.rng.bit_generator.state, 'python_rng': random.getstate(),
            'numpy_rng': np.random.get_state(), 'torch_rng': torch.get_rng_state(),
            'episodes': self.episodes, 'transitions': self.transitions, 'gradient_updates': self.gradient_updates,
            'metrics': self.metrics, 'selection': self.selection, 'last_update': self.last_update,
            'resources': self.resources, 'action_counts': self.action_counts, 'incident_counts': self.incident_counts,
            'policy_hash': self.policy.hash}

    def save(self, path):
        import torch
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        with temporary.open('wb') as stream:
            torch.save(self.state(), stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def restore(self, path):
        import torch
        # Checkpoints are local experiment outputs, never downloaded/untrusted files.
        state = torch.load(path, map_location='cpu', weights_only=False)
        if state['identity'] != self.identity or state['feature_schema'] != self.policy.schema.canonical_json:
            raise ValueError('DQN checkpoint source/input/model/schema/lock identity changed')
        self.policy.online.load_state_dict(state['online'])
        self.target.load_state_dict(state['target'])
        self.optimizer.load_state_dict(state['optimizer'])
        if self.policy.hash != state['policy_hash']:
            raise ValueError('DQN policy checksum mismatch')
        self.buffer.rows, self.buffer.cursor = state['replay'], state['cursor']
        self.sampler.bit_generator.state = state['sampler']
        self.replay_rng.bit_generator.state = state['replay_rng']
        self.policy.rng.bit_generator.state = state['policy_rng']
        random.setstate(state['python_rng'])
        np.random.set_state(state['numpy_rng'])
        torch.set_rng_state(state['torch_rng'])
        for key in ('episodes', 'transitions', 'gradient_updates', 'metrics', 'selection', 'last_update',
                    'resources', 'action_counts', 'incident_counts'):
            setattr(self, key, state[key])
        return self


def train_loop(factory, identifiers, validate, schema, identity, settings, destination, *, seed=11,
               resume=False, stop_after=None, sampler_groups=None, neural_settings=None):
    """Save only at episode boundaries, select best only on held-out validation."""
    from copy import deepcopy
    from .contracts import digest
    from .dataset import atomic_json
    from .train import runtime_hash
    destination = Path(destination)
    latest = destination / 'latest.pt'
    trainer = DQNTrainer(schema, {**identity, 'runtime_hash': runtime_hash(), 'training': settings,
        'sampler_ids': tuple(identifiers), 'sampler_groups': sampler_groups}, seed=seed, settings=neural_settings)
    if resume:
        trainer.restore(latest)
    elif latest.exists():
        raise ValueError('existing DQN run requires explicit resume')
    from .training_resources import TrainingResources
    resources = TrainingResources(trainer.resources)
    total = settings['episodes']
    boundary = min(total, stop_after) if stop_after is not None else total
    if boundary < trainer.episodes:
        raise ValueError('stop boundary precedes saved episode')
    for episode in range(trainer.episodes, boundary):
        epsilon = settings['epsilon_start'] + (settings['epsilon_end']-settings['epsilon_start']) * min(1, episode/settings['epsilon_decay_episodes'])
        if sampler_groups:
            # Select a diagnosis set, then a mechanism stratum, then an incident.
            groups = tuple(sampler_groups)
            strata = sampler_groups[groups[int(trainer.sampler.integers(len(groups)))]]
            mechanisms = tuple(strata)
            ids = strata[mechanisms[int(trainer.sampler.integers(len(mechanisms)))]]
            identifier = ids[int(trainer.sampler.integers(len(ids)))]
        else:
            identifier = identifiers[int(trainer.sampler.integers(len(identifiers)))]
        env, truth = factory(identifier)
        try:
            outcome = trainer.episode(env, truth, epsilon)
        finally:
            env.close()
        trainer.incident_counts[identifier] = trainer.incident_counts.get(identifier, 0) + 1
        for row in outcome['trace']:
            trainer.action_counts[row['action']] = trainer.action_counts.get(row['action'], 0) + 1
        if trainer.episodes % settings['validation_interval'] == 0 or trainer.episodes == total:
            summary = validate(deepcopy(trainer.policy))
            metric = {'episode': trainer.episodes, 'epsilon': epsilon, 'validation': summary,
                      'transitions': trainer.transitions, 'gradient_updates': trainer.gradient_updates,
                      'replay_size': len(trainer.buffer.rows), 'last_update': trainer.last_update}
            trainer.metrics.append(metric)
            trainer.resources = resources.snapshot()
            metric.update(resources=trainer.resources, training_action_counts=dict(trainer.action_counts),
                          training_incident_counts=dict(trainer.incident_counts))
            value = (summary['mean_return'], -summary['mean_credits'])
            if trainer.selection is None or value > tuple(trainer.selection['value']):
                trainer.selection = {'episode': trainer.episodes, 'value': list(value)}
                trainer.save(destination / 'best.pt')
        if trainer.episodes % 50 == 0 or trainer.episodes == boundary:
            trainer.resources = resources.snapshot()
            trainer.save(latest)
            atomic_json(destination / 'metrics.json', trainer.metrics)
    return {'passed': True, 'complete': trainer.episodes == total, 'episodes_completed': trainer.episodes,
            'gradient_updates': trainer.gradient_updates, 'transitions': trainer.transitions,
            'selection': trainer.selection, 'latest': str(latest), 'policy_hash': trainer.policy.hash,
            'training_only_replay': True, 'resources': resources.snapshot(),
            'training_action_counts': trainer.action_counts, 'training_incident_counts': trainer.incident_counts}
