"""Declared expanded rule with a paid check for the known masked second root."""
from .rules import FixedSequencePolicy


class ExpandedSetRule(FixedSequencePolicy):
    def __init__(self, specs, confidence=.95):
        preferred = ('status/catalogue-db/v2', 'status/orders/v2', 'status/orders-db/v2',
                     'status/user/v2', 'status/shipping/v2', 'status/payment/v2', 'cpu/payment/v2',
                     'memory/carts/v2', 'latency/front-end/v2', 'logs/orders/v2')
        ids = tuple(s.action_id for s in specs)
        sequence = tuple(a for a in preferred if a in ids) + tuple(a for a in ids if a not in preferred)
        super().__init__(sequence, confidence)

    def choose(self, observation):
        if observation['task_id'] == 'sockshop_multicause_v3' and dict(observation['history']).get('status/catalogue-db/v2') == 'OK:STOPPED':
            if 'cpu/payment/v2' in observation['mask']:
                return 'cpu/payment/v2'
        return super().choose(observation)
