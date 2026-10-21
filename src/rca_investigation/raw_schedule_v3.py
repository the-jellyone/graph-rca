"""One owner, frozen sessions, immutable v3 captures and accepted-slot resume."""
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4
import hashlib
import json
import shutil
import requests
import yaml
from .capture import capture_lease, RecoveryFailed
from .capture_store import write_raw, read_raw
from .capture_v3 import collect_window, verify_components
from .contracts import canonical, digest
from .contracts_v3 import IncidentPrivateV3
from .dataset import atomic_json
from .faults_v3 import LabDriverV3, FaultTransaction
from .raw_schedule import settle_states
from .schedule_v3 import SCENARIOS, validate_schedule
from .topology import sockshop_topology
from .workload_v3 import WorkloadV3, shipping_health_probe, operation_succeeded
from .backends.container_stats import ContainerStatsCollector
from .train import runtime_hash


def healthy_window(raw):
    return not raw['workload_errors'] and all(
        len(rows := [r for r in raw['requests'] if r['operation'] == operation]) >= 20 and
        all(operation_succeeded(r) for r in rows) for operation in ('auth', 'checkout', 'shipping_health'))


def capture_raw_v3(root, config_path, schedule_path, *, dry_run=False, stop_after=None):
    from .config import load_config
    root = Path(root)
    config = load_config(config_path, sockshop_topology())
    if config.values['task_id'] != 'sockshop_multicause_v3':
        raise ValueError('the primary physical cohort uses the multi-cause profile; single is a declared derivation')
    schedule = yaml.safe_load(Path(schedule_path).read_text())
    schedule_hash = validate_schedule(schedule)
    declaration = json.loads((root / 'experiments/v3/experiment-declaration-final.json').read_text())
    registry = yaml.safe_load((root / config.values['paths']['resource_registry']).read_text())
    if not registry['passed']:
        raise ValueError('v3 capability/workload gate has not passed')
    paths = config.values['paths']
    audit, raw_root = root / paths['runs'] / 'capture', root / paths['raw_dataset']
    roles = {s['id']: s['role'] for s in schedule['sessions']}
    slots = [(s['id'], i, name) for s in schedule['sessions'] for i, name in enumerate(s['scenarios'])]
    identity = {'runtime_hash': runtime_hash(), 'config_hash': config.hash, 'schedule_hash': schedule_hash,
                'registry_hash': registry['hash'], 'declaration_hash': declaration['hash'],
                'lock_sha256': hashlib.sha256((root / 'uv.lock').read_bytes()).hexdigest(),
                'helper_sha256': hashlib.sha256((root / 'lab/adaptive-helper/rca-helper-lease').read_bytes()).hexdigest()}
    if dry_run:
        return {'passed': True, 'dry_run': True, 'slots': len(slots), 'identity': identity}
    frozen_source = json.loads((root / 'experiments/v3/final-runtime-source.json').read_text())
    if frozen_source['runtime_hash'] != identity['runtime_hash']:
        raise ValueError('complete and freeze v3 software before primary physical collection')
    pilots = json.loads((root / paths['runs'] / 'capture_pilot/schedule.json').read_text())
    if not pilots['complete'] or len(pilots['accepted']) != 30:
        raise ValueError('thirty independently accepted mechanics pilots required')
    if identity['helper_sha256'] != declaration['provenance']['helper_sha256']:
        raise ValueError('declared helper changed')
    state_path = audit / 'schedule.json'
    state = {'schema_version': 3, 'identity': identity, 'schedule_hash': schedule_hash,
             'roles': roles, 'accepted': {}, 'complete': False}
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state['identity'] != identity or state['roles'] != roles:
            raise ValueError('primary capture identity changed; accepted slots cannot be silently reused')
    atomic_json(state_path, state)
    driver = LabDriverV3(sockshop_topology(), root, registry['resources'])
    driver.resolve()
    stats = ContainerStatsCollector(registry['resources'])
    shared = root / 'runs/adaptive_v2/lab'
    for session, index, stratum in slots:
        key = f'{session}/{index}'
        if key in state['accepted']:
            payloads, raw_hash = read_raw(raw_root / state['accepted'][key], public_only=False)
            if raw_hash != state['bundle_hashes'][key] or payloads['capture_receipt.json']['stratum'] != stratum:
                raise ValueError('accepted raw slot changed')
            continue
        if stop_after is not None and len(state['accepted']) >= stop_after:
            break
        if (audit / 'pause-requested.json').exists():
            state['pause_state'] = True
            atomic_json(state_path, state)
            break
        if shutil.disk_usage(root).free < 10*1024**3:
            raise RuntimeError('primary collection requires ten GiB free; accepted captures will not be pruned')
        driver.resolve()
        incident = str(uuid4())
        receipt = {'incident_id': incident, 'physical_episode': str(uuid4()), 'capture_session': session,
                   'lab_batch': schedule['lab_batch'], 'role': roles[session], 'stratum': stratum,
                   'status': 'CREATED', 'recovery_confirmed': False, 'identity': identity}
        workload = WorkloadV3(shipping_probe=lambda: shipping_health_probe(driver))
        transaction = None
        raw = window = None
        with capture_lease(shared):
            legacy = root / 'runs/integration_v1/captures/sockshop_v1'
            if any((legacy / n).exists() for n in ('capture.lock', 'blocked-lab.json')):
                raise RecoveryFailed('legacy lab ownership or recovery gate is unresolved')
            try:
                expected = {r: 'running' for r in driver.topology.resources}
                if driver.states() != expected:
                    raise RuntimeError('primary baseline requires all lab resources running')
                workload.prepare()
                baseline_window, baseline = collect_window(driver, workload, stats, registry['resources'], expected, ingestion_seconds=0)
                receipt.update(baseline_window=baseline_window, baseline=baseline)
                if not healthy_window(baseline):
                    raise RuntimeError('healthy baseline lacks successful operation coverage')
                transaction = FaultTransaction(driver, shared / 'blocked-lab.json', owner=incident)
                components = transaction.inject(SCENARIOS[stratum])
                for row in components.values():
                    if row['mechanism'] == 'container_stopped': expected[row['resource_id']] = 'exited'
                settle_states(driver, expected, 30)
                window, raw = collect_window(driver, workload, stats, registry['resources'], expected)
                receipt.update(window=window, components=components)
                receipt['verification'] = verify_components(components, window, raw, registry['resources'], driver)
                if not components and not healthy_window(raw):
                    raise RuntimeError('healthy observation degraded')
                receipt['effect_confirmed'] = True
            except BaseException as exc:
                receipt.update(status='REJECTED', error=f'{type(exc).__name__}: {exc}')
                if raw is not None:
                    atomic_json(audit / 'rejected-observations' / (incident + '.json'), {'window': window, 'measurements': raw})
                raise
            finally:
                try:
                    if transaction is not None: receipt['component_recovery'] = transaction.recover()
                    deadline = monotonic()+90
                    def healthy_recovery():
                        while monotonic() < deadline:
                            try:
                                if driver.states() == {r: 'running' for r in driver.topology.resources}:
                                    workload.prepare()
                                    rows = [workload.fresh_auth(), workload.checkout(), workload.shipping_health()]
                                    receipt.setdefault('recovery_checks', []).append(rows)
                                    memory_ok = True
                                    if transaction is not None:
                                        for component in transaction.state['components']:
                                            if component['mechanism'] == 'memory_pressure':
                                                sample = next(r for r in stats.poll() if r['resource_id'] == 'carts')
                                                limit = registry['resources']['carts']['memory_limit_bytes']
                                                memory_ok &= sample['availability'] == 'OK' and sample['memory_bytes'] <= component['baseline_memory_bytes']+max(16*1024**2, limit*.05)
                                    if all(operation_succeeded(r) for r in rows) and memory_ok:
                                        workload.reset()
                                        return True
                            except (RuntimeError, requests.RequestException) as exc:
                                receipt.setdefault('recovery_errors', []).append(str(exc))
                            sleep(2)
                        return False
                    if transaction is not None: transaction.confirm_recovery(healthy_recovery)
                    elif not healthy_recovery(): raise RecoveryFailed('baseline recovery failed')
                    receipt['recovery_confirmed'] = True
                except BaseException as exc:
                    receipt.update(status='RECOVERY_FAILED', recovery_error=str(exc))
                    if transaction is None:
                        transaction = FaultTransaction(driver, shared / 'blocked-lab.json', owner=incident)
                        transaction.save()
                    raise
                finally:
                    atomic_json(audit / 'attempts' / (incident + '.json'), receipt)
                    workload.close()
            receipt['status'] = 'ACCEPTED'
            public = {'schema_version': 3, 'incident_id': incident, 'task_id': config.values['task_id'],
                      'topology_hash': driver.topology.hash, 'registry_hash': digest(registry['resources']),
                      'entry_resource': 'front-end', 'entry_protocol': 'manual_frontend', **window}
            mechanisms = SCENARIOS[stratum]
            private = IncidentPrivateV3(3, incident, config.values['task_id'], tuple(c for c, _ in mechanisms),
                mechanisms, canonical(components), canonical(receipt['verification']), canonical(receipt['component_recovery']))
            write_raw(raw_root, incident, public, raw, private, receipt)
            _, raw_hash = read_raw(raw_root / incident, public_only=False)
            state['accepted'][key] = incident
            state.setdefault('bundle_hashes', {})[key] = raw_hash
            atomic_json(state_path, state)
    state['complete'] = len(state['accepted']) == 440
    atomic_json(state_path, state)
    return state
