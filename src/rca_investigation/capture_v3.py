"""Common-window measurements with independent operation streams and receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep
import requests
from .backends.evidence_v2 import collect_logs
from .contracts import timestamp
from .metrics.cpu import summarize as cpu_summary


def verify_common_overlap(receipts, window):
    start, end = timestamp(window['window_start']), timestamp(window['window_end'])
    if (end-start).total_seconds() != 120:
        raise ValueError('common observation must last 120 seconds')
    for row in receipts.values():
        if row['status'] != 'ACTIVE' or timestamp(row['injected_at']) > start:
            raise ValueError('component not effective before observation')
        if row['mechanism'] in ('cpu_contention', 'memory_pressure') and timestamp(row['expires_at']) <= end:
            raise ValueError('resource helper expires before the common window ends')
    return True


def collect_window(driver, workload, stats, registry, expected_states, *, ingestion_seconds=10):
    from .backends.container_stats_v3 import StreamingStatsCollector
    with StreamingStatsCollector(registry) as stream:
        return _collect_window(driver, workload, stream, registry, expected_states, ingestion_seconds=ingestion_seconds)


def _collect_window(driver, workload, stats, registry, expected_states, *, ingestion_seconds=10):
    if type(ingestion_seconds) is not int or not 0 <= ingestion_seconds <= 10:
        raise ValueError('unbounded telemetry ingestion')
    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=120)
    deadline = monotonic() + 120
    cancelled = Event()
    raw = {'stats': [], 'requests': [], 'status': {}, 'status_history': [], 'workload_errors': []}

    def traffic(operation, call):
        rows, errors = [], []
        next_due = monotonic()
        while monotonic() < deadline and not cancelled.is_set():
            try:
                row = call()
                if start <= timestamp(row['sample_time']) <= end:
                    rows.append(row)
            except (RuntimeError, requests.RequestException) as exc:
                errors.append({'operation': operation, 'sample_time': datetime.now(timezone.utc).isoformat(),
                               'reason': type(exc).__name__})
            next_due += 2
            cancelled.wait(min(max(0, next_due-monotonic()), max(0, deadline-monotonic())))
        return rows, errors

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(traffic, op, call) for op, call in (
            ('auth', workload.fresh_auth), ('checkout', workload.checkout), ('shipping_health', workload.shipping_health))]
        try:
            while monotonic() < deadline:
                began = monotonic()
                states = driver.states()
                if states != expected_states:
                    raise RuntimeError('resource state changed during the common window')
                sample_time = datetime.now(timezone.utc).isoformat()
                raw['status_history'].append({'sample_time': sample_time, 'states': states})
                for resource in registry:
                    raw['status'][resource] = {'resource': resource, 'container_id': driver.ids[resource],
                                               'state': states[resource], 'sample_time': sample_time}
                raw['stats'].extend(row for row in stats.poll() if start <= timestamp(row['sample_time']) <= end)
                cancelled.wait(min(max(0, 2-(monotonic()-began)), max(0, deadline-monotonic())))
        finally:
            cancelled.set()
        for future in futures:
            rows, errors = future.result()
            raw['requests'].extend(rows)
            raw['workload_errors'].extend(e for e in errors if start <= timestamp(e['sample_time']) <= end)
    sleep(ingestion_seconds)
    with requests.Session() as session:
        raw['logs'] = collect_logs(session, registry, start.isoformat(), end.isoformat())
    raw['collected_at'] = datetime.now(timezone.utc).isoformat()
    raw['registry'] = registry
    return {'window_start': start.isoformat(), 'window_end': end.isoformat()}, raw


def verify_components(receipts, window, raw, registry, driver=None):
    verify_common_overlap(receipts, window)
    checks = {}
    for cause, receipt in receipts.items():
        resource = receipt['resource_id']
        if receipt['mechanism'] == 'container_stopped':
            history = raw['status_history']
            times = [timestamp(row['sample_time']) for row in history]
            start, end = timestamp(window['window_start']), timestamp(window['window_end'])
            coverage = len(history) >= 48 and times and (times[0]-start).total_seconds() <= 5 and (end-times[-1]).total_seconds() <= 5
            passed = bool(coverage and all(row['states'][resource] in ('exited', 'dead') for row in history))
            checks[cause] = {'passed': passed, 'state_sample_count': len(history)}
        elif receipt['mechanism'] == 'cpu_contention':
            item = registry[resource]
            samples = [row for row in raw['stats'] if row['resource_id'] == resource]
            availability, _, evidence, reason = cpu_summary(samples, resource, item['container_id'], item['cpu_quota_cores'],
                window['window_start'], window['window_end'], .8)
            checks[cause] = {'passed': availability == 'OK' and evidence['median'] >= .8 and evidence['high_fraction'] >= .5,
                             'availability': availability, 'evidence': evidence, 'reason': reason}
        elif receipt['mechanism'] == 'memory_pressure':
            from .metrics.memory import summarize
            item = registry[resource]
            samples = [r for r in raw['stats'] if r['resource_id'] == resource]
            availability, _, evidence, reason = summarize(samples, resource, item['container_id'], item['memory_limit_bytes'],
                window['window_start'], window['window_end'], pressure=.75)
            checks[cause] = {'passed': availability == 'OK' and evidence['limit_fraction'] >= .75,
                             'availability': availability, 'evidence': evidence, 'scope': 'resource_pressure_only', 'reason': reason}
        elif receipt['mechanism'] == 'dependency_latency':
            if driver is None:
                raise ValueError('path verification requires the registered controller')
            current = driver.proxy_control()
            forwarded = current['forwarded_requests']-receipt['proxy_before']['forwarded_requests']
            checks[cause] = {'passed': current['delay_ms'] == 800 and forwarded >= 20,
                             'delay_ms': current['delay_ms'], 'forwarded_requests': forwarded}
        else:
            raise ValueError('component verification is not registered')
    if any(not check['passed'] for check in checks.values()):
        raise RuntimeError('independent component verification failed')
    return checks
