"""Fixed session roles and independently randomized eleven-stratum sessions."""
import random
from .contracts import digest

SCENARIOS = {
    'healthy': (),
    'catalogue_db_stop': (('resource/catalogue-db', 'container_stopped'),),
    'orders_stop': (('resource/orders', 'container_stopped'),),
    'payment_cpu': (('resource/payment', 'cpu_contention'),),
    'orders_payment_delay': (('edge/orders/payment', 'dependency_latency'),),
    'carts_memory': (('resource/carts', 'memory_pressure'),),
    'payment_stop': (('resource/payment', 'container_stopped'),),
    'orders_db_stop': (('resource/orders-db', 'container_stopped'),),
    'user_stop': (('resource/user', 'container_stopped'),),
    'shipping_stop': (('resource/shipping', 'container_stopped'),),
    'catalogue_db_payment_cpu': (('resource/catalogue-db', 'container_stopped'), ('resource/payment', 'cpu_contention')),
}


def evaluation_schedule(seed=11):
    rng = random.Random(seed)
    sessions = []
    for role, count in [('train', 20), ('validation', 10), ('test', 10)]:
        for index in range(count):
            order = list(SCENARIOS)
            rng.shuffle(order)
            sessions.append({'id': f'v3-{role}-{index+1:02d}', 'role': role, 'scenarios': order})
    return {'schema_version': 3, 'task_id': 'sockshop_multicause_v3', 'seed': seed,
            'lab_batch': 'v3-primary-protocol120', 'sessions': sessions,
            'protocol': {'baseline_seconds': 120, 'settling_seconds': 30, 'observation_seconds': 120,
                         'ingestion_seconds': 10, 'operation_interval_seconds': 2,
                         'operations': ['auth', 'checkout', 'shipping_health']}}


def validate_schedule(schedule):
    expected = evaluation_schedule(11)
    if schedule != expected:
        raise ValueError('primary collection must use the preassigned complete session schedule')
    return digest(schedule)
