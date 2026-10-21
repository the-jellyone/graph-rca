"""Registered cause sets, exact utility and separate descriptive root metrics."""
from .contracts import canonical, unique, real

ATOMIC_CAUSES = tuple(sorted(('resource/catalogue-db', 'resource/orders', 'resource/payment',
    'resource/carts', 'resource/orders-db', 'resource/user', 'resource/shipping', 'edge/orders/payment')))
PAIR = ('resource/catalogue-db', 'resource/payment')
SINGLE_HYPOTHESES = ((),) + tuple((cause,) for cause in ATOMIC_CAUSES)
HYPOTHESES = SINGLE_HYPOTHESES + (PAIR,)
PROFILES = {'sockshop_single_v3': SINGLE_HYPOTHESES, 'sockshop_multicause_v3': HYPOTHESES}


def cause_set(values, task_id='sockshop_multicause_v3'):
    unique(values)
    if task_id not in PROFILES or not set(values) <= set(ATOMIC_CAUSES):
        raise ValueError('unknown task or cause')
    result = tuple(sorted(values))
    if result not in PROFILES[task_id]:
        raise ValueError('unsupported cause combination')
    return result


def hypothesis_key(values):
    return canonical(cause_set(values))


def terminal_utility(decision, truth, *, correct=1., incorrect=0., defer=.25):
    truth = cause_set(truth)
    for value in (correct, incorrect, defer):
        real(value)
    return defer if decision.decision_type == 'DEFER' else correct if decision.cause_ids == truth else incorrect


def root_metrics(decision, truth):
    """DEFER is uncovered, scores no roots and is never a healthy prediction.

    Empty/empty has precision, F1 and Jaccard one. Root recall excludes healthy
    truth; a wrong healthy prediction has zero precision and Jaccard.
    """
    truth = set(cause_set(truth))
    covered = decision.decision_type != 'DEFER'
    predicted = set(decision.cause_ids) if covered else set()
    tp, fp, fn = len(truth & predicted), len(predicted - truth), len(truth - predicted)
    healthy_correct = covered and not truth and not predicted
    precision = tp / len(predicted) if predicted else float(healthy_correct)
    recall = tp / len(truth) if truth else None
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else float(healthy_correct)
    return {'exact': covered and predicted == truth, 'covered': covered, 'precision': precision,
            'recall': recall, 'f1': f1,
            'jaccard': tp / len(truth | predicted) if truth | predicted else float(healthy_correct),
            'healthy_specificity': float(healthy_correct) if not truth else None,
            'true_positive_roots': tp, 'false_positive_roots': fp, 'false_negative_roots': fn}


def root_marginals(posterior):
    import math
    if not posterior or any(not math.isfinite(p) or p < 0 for p in posterior.values()) or not math.isclose(sum(posterior.values()), 1.):
        raise ValueError('invalid set posterior')
    for values in posterior:
        cause_set(values)
    return {cause: sum(p for values, p in posterior.items() if cause in values) for cause in ATOMIC_CAUSES}
