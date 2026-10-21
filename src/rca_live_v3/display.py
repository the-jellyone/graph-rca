"""Readable controller output; machine records remain in the event journal."""
def render(kind,payload):
    if kind=='latency':
        return f"[Overall latency {payload['elapsed_seconds']:.1f} s] {payload['stage']}."
    if kind=='probe':
        return f"Probe {payload['action']}: {payload['response']}. Credits {payload['credits']}/10. Choice {payload['decision_latency_ms']:.2f} ms."
    if kind=='diagnosis':
        d=payload['decision']
        roots=', '.join(d['cause_ids']) or 'healthy'
        return f"Diagnosis: {roots if d['decision_type']=='DIAGNOSE' else 'DEFER'}. Confidence {d['confidence']:.1%}. Probes {payload['probes']}."
    if kind=='result':
        timing=f" Overall latency: {payload['overall_latency_seconds']:.1f} s from injection request to final outcome." if 'overall_latency_seconds' in payload else ''
        return f"Run {payload['status']}. Agent repair: {payload.get('agent_repair',False)}. Recovery verified: {payload.get('health',{}).get('confirmed',False)}.{timing}"
    return str(payload.get('message',kind))
