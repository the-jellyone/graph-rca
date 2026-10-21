"""Independent decision scores and paired session uncertainty."""
from collections import Counter
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from .contracts import digest
from .dataset import load_private_label, read_split
from .environment import score_decision
from .experiment import label_name, replay_environment


def investigate(env, policy, policy_hash):
    trace=[]
    while not env.terminated:
        observation=env.observation()
        started=perf_counter()
        action=policy.choose(observation)
        elapsed=(perf_counter()-started)*1000
        _,reward,done=env.step(action,policy_hash=policy_hash)
        trace.append({'action':action,'reward':reward,'terminal':done,'decision_latency_ms':elapsed,
                      'stats':{k:v for k,v in getattr(policy,'stats',{}).items() if k!='values'},
                      'evidence_ids':list(env.ledger.evidence_ids),'credits':env.ledger.credits})
    return {'decision':env.decision.to_dict(),'trace':trace,'credits':env.ledger.credits,'probes':env.probes,
            'probe_return':env.probe_return,'probabilities':env.model.diagnosis(env.history),
            'packets':[p.to_dict() for p in env.ledger.packets]}


def evaluate_paths(paths, specs, model, config, policy, policy_hash='evaluation'):
    outcomes=[]
    for path in paths:
        env=replay_environment(path,specs,model,config)
        try:
            outcome=investigate(env,policy,policy_hash)
            # Private truth enters only after the public investigation has ended.
            truth=label_name(load_private_label(path))
            receipt=json.loads((Path(path)/'capture_receipt.json').read_text(encoding='utf-8'))
            decision=env.decision
            v3 = getattr(decision, 'schema_version', 1) == 3
            outcome.update(incident_id=env.incident.incident_id,truth=truth,capture_session=receipt['capture_session'],
                           correct=(decision.decision_type == 'DIAGNOSE' and decision.cause_ids == truth) if v3 else decision.prediction==truth,
                           covered=(decision.decision_type != 'DEFER') if v3 else decision.prediction!='DEFER',
                           return_value=outcome['probe_return']+score_decision(decision,truth,
                               correct=config.values['objective']['correct'],incorrect=config.values['objective']['incorrect'],defer=config.values['objective']['defer']))
            if v3:
                outcome['stratum'] = receipt['stratum']
            outcomes.append(outcome)
        finally:
            env.close()
    return summarize(outcomes,model.classes),outcomes


def summarize(outcomes, classes):
    if not outcomes:
        raise ValueError('empty evaluation set')
    if outcomes[0]['decision'].get('schema_version') == 3:
        return summarize_sets(outcomes, classes)
    n=len(outcomes)
    covered=[o for o in outcomes if o['covered']]
    probes=sum(o['probes'] for o in outcomes)
    packets=[p for o in outcomes for p in o['packets']]
    steps=[t for o in outcomes for t in o['trace']]
    bins=[]
    for low in np.arange(0,1,.1):
        rows=[o for o in covered if low <= o['decision']['confidence'] < low+.1 or low>.89 and o['decision']['confidence']==1]
        if rows:
            bins.append({'lower':float(low),'count':len(rows),'mean_confidence':float(np.mean([o['decision']['confidence'] for o in rows])),
                         'accuracy':float(np.mean([o['correct'] for o in rows]))})
    brier=np.mean([sum((o['probabilities'][c]-(o['truth']==c))**2 for c in classes) for o in outcomes])
    report = {'physical_incidents':n,'accuracy':sum(o['correct'] for o in outcomes)/n,'coverage':len(covered)/n,
            'selective_accuracy':sum(o['correct'] for o in covered)/len(covered) if covered else None,
            'mean_credits':float(np.mean([o['credits'] for o in outcomes])),
            'mean_probes':probes/n,'mean_return':float(np.mean([o['return_value'] for o in outcomes])),
            'tool_failure_rate':sum(p['availability']!='OK' for p in packets)/len(packets) if packets else 0,
            'repeated_request_attempts':0,'termination_reasons':dict(Counter(o['decision']['stop_reason'] for o in outcomes)),
            'confusion':{c:dict(Counter(o['decision']['prediction'] for o in outcomes if o['truth']==c)) for c in classes},
            'brier_score':float(brier),'calibration_bins':bins,
            'decision_latency_ms':float(np.mean([s['decision_latency_ms'] for s in steps])),
            'planner_expanded_states':sum(s['stats'].get('expanded_states',0) for s in steps),
            'fallback_frequency':sum(s['stats'].get('fallback',False) for s in steps)/len(steps),
            'unseen_state_fraction':sum(s['stats'].get('unseen',False) for s in steps)/len(steps),
            'lightly_visited_fraction':sum(s['stats'].get('lightly_visited',False) for s in steps)/len(steps)}
    if any(c.startswith(('resource/', 'edge/')) for c in classes):
        for kind, prefix in (('resource', 'resource/'), ('path', 'edge/')):
            rows = [outcome for outcome in outcomes if outcome['truth'].startswith(prefix)]
            report[kind + '_accuracy'] = sum(row['correct'] for row in rows) / len(rows) if rows else None
        report['mechanism_prediction_enabled'] = False
    return report


def summarize_sets(outcomes, classes):
    from .contracts_v3 import DecisionV3
    from .diagnosis_sets import hypothesis_key, root_metrics, ATOMIC_CAUSES
    metrics = [root_metrics(DecisionV3.from_json(json.dumps(o['decision'])), tuple(o['truth'])) for o in outcomes]
    covered = [o for o in outcomes if o['covered']]
    healthy = [m['healthy_specificity'] for m in metrics if m['healthy_specificity'] is not None]
    pairs = [o for o in outcomes if len(o['truth']) == 2]
    per_root = {}
    for cause in ATOMIC_CAUSES:
        tp = sum(cause in o['truth'] and cause in o['decision']['cause_ids'] for o in covered)
        fp = sum(cause not in o['truth'] and cause in o['decision']['cause_ids'] for o in covered)
        fn = sum(cause in o['truth'] and (not o['covered'] or cause not in o['decision']['cause_ids']) for o in outcomes)
        per_root[cause] = {'tp': tp, 'fp': fp, 'fn': fn, 'precision': tp/(tp+fp) if tp+fp else None,
                           'recall': tp/(tp+fn) if tp+fn else None, 'f1': 2*tp/(2*tp+fp+fn) if tp+fp+fn else None}
    steps = [step for o in outcomes for step in o['trace']]
    timings = [s['decision_latency_ms'] for s in steps]
    packets = [p for o in outcomes for p in o['packets']]
    strata = {name: [o for o in outcomes if o.get('stratum') == name] for name in sorted({o.get('stratum', 'unspecified') for o in outcomes})}
    sessions = {name: [o for o in outcomes if o['capture_session'] == name] for name in sorted({o['capture_session'] for o in outcomes})}
    calibration = []
    for low in np.arange(0, 1, .1):
        rows = [o for o in covered if low <= o['decision']['confidence'] < low+.1 or low > .89 and o['decision']['confidence'] == 1]
        if rows: calibration.append({'lower': float(low), 'count': len(rows), 'mean_confidence': float(np.mean([o['decision']['confidence'] for o in rows])), 'accuracy': float(np.mean([o['correct'] for o in rows]))})
    return {'physical_incidents': len(outcomes), 'accuracy': float(np.mean([o['correct'] for o in outcomes])),
        'selective_accuracy': float(np.mean([o['correct'] for o in covered])) if covered else None,
        'coverage': len(covered)/len(outcomes), 'defer_rate': 1-len(covered)/len(outcomes),
        'healthy_specificity': float(np.mean(healthy)) if healthy else None,
        'pair_count': len(pairs), 'pair_correct': sum(o['correct'] for o in pairs),
        'pair_accuracy': float(np.mean([o['correct'] for o in pairs])) if pairs else None,
        'missing_second_root_rate': sum(len(set(o['truth']) & set(o['decision']['cause_ids'])) == 1 for o in pairs)/len(pairs) if pairs else None,
        'extra_root_rate': float(np.mean([bool(set(o['decision']['cause_ids']) - set(o['truth'])) for o in outcomes])),
        'mean_return': float(np.mean([o['return_value'] for o in outcomes])),
        'mean_credits': float(np.mean([o['credits'] for o in outcomes])),
        'mean_probes': float(np.mean([o['probes'] for o in outcomes])),
        'mean_jaccard': float(np.mean([m['jaccard'] for m in metrics])), 'per_root': per_root,
        'mean_root_precision': float(np.mean([m['precision'] for m in metrics])),
        'mean_root_recall': float(np.mean([m['recall'] for m in metrics if m['recall'] is not None])) if any(m['recall'] is not None for m in metrics) else None,
        'mean_root_f1': float(np.mean([m['f1'] for m in metrics])),
        'tool_failure_rate': sum(p['availability'] != 'OK' for p in packets)/len(packets) if packets else 0.,
        'action_use': dict(Counter(s['action'] for s in steps)),
        'termination_reasons': dict(Counter(o['decision']['stop_reason'] for o in outcomes)),
        'decision_latency_ms': float(np.mean(timings)) if timings else 0.,
        'decision_median_ms': float(np.median(timings)) if timings else 0.,
        'decision_p95_ms': float(np.quantile(timings, .95)) if timings else 0.,
        'planner_expanded_states': sum(s['stats'].get('expanded_states', 0) for s in steps),
        'fallback_frequency': sum(s['stats'].get('fallback', False) for s in steps)/len(steps) if steps else 0.,
        'unseen_state_fraction': sum(s['stats'].get('unseen', False) for s in steps)/len(steps) if steps else 0.,
        'strata': {name: {'count': len(rows), 'correct': sum(o['correct'] for o in rows), 'accuracy': float(np.mean([o['correct'] for o in rows]))} for name, rows in strata.items()},
        'sessions': {name: {'count': len(rows), 'mean_return': float(np.mean([o['return_value'] for o in rows])), 'mean_credits': float(np.mean([o['credits'] for o in rows]))} for name, rows in sessions.items()},
        'calibration_bins': calibration,
        'brier_score': float(np.mean([sum((o['probabilities'][c] - (hypothesis_key(tuple(o['truth'])) == c))**2 for c in classes) for o in outcomes])),
        'confusion': {c: dict(Counter('DEFER' if not o['covered'] else hypothesis_key(tuple(o['decision']['cause_ids']))
            for o in outcomes if hypothesis_key(tuple(o['truth'])) == c)) for c in classes},
        'mechanism_prediction_enabled': False}


def paired_session_interval(first, second, seed=11, samples=2000):
    left={o['incident_id']:o for o in first}
    right={o['incident_id']:o for o in second}
    if set(left)!=set(right):
        raise ValueError('paired comparisons require identical incident IDs')
    sessions={}
    for incident,o in left.items():
        if o['capture_session']!=right[incident]['capture_session']:
            raise ValueError('capture session mismatch')
        sessions.setdefault(o['capture_session'],[]).append(o['return_value']-right[incident]['return_value'])
    ids=sorted(sessions)
    rng=np.random.default_rng(seed)
    bootstrap=[]
    for _ in range(samples):
        values=[d for session in rng.choice(ids,size=len(ids),replace=True) for d in sessions[session]]
        bootstrap.append(float(np.mean(values)))
    return {'mean_paired_return_difference':float(np.mean([d for values in sessions.values() for d in values])),
            'session_bootstrap_95':np.quantile(bootstrap,[.025,.975]).tolist(),'independent_sessions':len(ids),
            'seed':seed,'resamples':samples}
