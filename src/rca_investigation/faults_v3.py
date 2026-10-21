"""Exact-identity stops and journalled component transactions with full cleanup."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from .faults_v2 import AdaptiveLabDriver
from .contracts import digest
from .dataset import atomic_json
from .diagnosis_sets import cause_set, PAIR
from .contracts_v3 import MECHANISMS
from .capture import RecoveryFailed

STOP_TARGETS = {'catalogue-db', 'orders', 'payment', 'orders-db', 'user', 'shipping'}


class LabDriverV3(AdaptiveLabDriver):
    def __init__(self, topology, root, registry, **kwargs):
        super().__init__(topology, root, **kwargs)
        self.registry = registry

    def resolve(self):
        result = super().resolve()
        if any(result[r] != item['container_id'] for r, item in self.registry.items()):
            raise ValueError('v3 registry container identity changed')
        output, _ = self.command(['inspect', *(result[r] for r in self.registry)])
        declaration = json.loads((Path(self.root) / 'experiments/v3/experiment-declaration-final.json').read_text())
        images = declaration['provenance']['docker_image_ids']
        for row in json.loads(output):
            resource = row['Config']['Labels']['com.docker.compose.service']
            item, host = self.registry[resource], row['HostConfig']
            quota = host.get('NanoCpus', 0)/1e9
            if not quota and host.get('CpuQuota', 0) > 0 and host.get('CpuPeriod', 0) > 0:
                quota = host['CpuQuota']/host['CpuPeriod']
            if row['Image'] != images[row['Id']] or quota != item['cpu_quota_cores'] or host['Memory'] != item['memory_limit_bytes']:
                raise ValueError('v3 image or resource limits changed')
        return result

    def inject_stop(self, resource):
        if resource not in STOP_TARGETS or resource not in self.registry:
            raise ValueError('unregistered v3 stop target')
        if self.state(resource)['state'] != 'running':
            raise RuntimeError('stop target is not initially running')
        _, receipt = self.command(['stop', '--time', '3', self.ids[resource]])
        return {**receipt, 'container_id': self.ids[resource], 'resource_id': resource, 'mechanism': 'container_stopped'}

    def recover(self, resource):
        if resource not in STOP_TARGETS or resource not in self.registry:
            raise ValueError('unregistered v3 recovery target')
        self.state(resource)
        _, receipt = self.command(['start', self.ids[resource]])
        return receipt

    def inject_cpu_lease(self, lease, seconds=210):
        return self.inject_resource_lease('payment', 'cpu', lease, seconds=seconds)

    def memory_allocation(self):
        from .backends.container_stats import ContainerStatsCollector
        row = next(r for r in ContainerStatsCollector(self.registry).poll() if r['resource_id'] == 'carts')
        if row['availability'] != 'OK':
            raise RuntimeError('memory injection requires measured healthy allocation margin')
        limit = self.registry['carts']['memory_limit_bytes']
        allocation = min(int(limit*.83)-row['memory_bytes'], int(limit*.5))
        if allocation <= 0:
            raise RuntimeError('healthy memory leaves no bounded pressure margin')
        return allocation, row['memory_bytes']

    def inject_resource_lease(self, resource, mode, lease, seconds=210, allocation_bytes=0):
        if (resource, mode) not in {('payment', 'cpu'), ('carts', 'memory')}:
            raise ValueError('unregistered leased resource')
        if type(seconds) is not int or not 1 <= seconds <= 240:
            raise ValueError('unbounded resource lease')
        if mode == 'cpu' and allocation_bytes != 0 or mode == 'memory' and (
                type(allocation_bytes) is not int or not 0 < allocation_bytes <= min(512*1024**2, self.registry[resource]['memory_limit_bytes']*.5)):
            raise ValueError('unbounded resource allocation')
        if self.state(resource)['state'] != 'running':
            raise RuntimeError('helper target is not running')
        binary = Path(self.root) / 'lab/adaptive-helper/rca-helper-lease'
        pin = json.loads((Path(self.root) / 'configs/lab_adaptive_v2_lease.json').read_text())
        if hashlib.sha256(binary.read_bytes()).hexdigest() != pin['helper_sha256']:
            raise ValueError('CPU helper binary changed')
        self.helper_leases[resource] = lease
        self.command(['cp', str(binary), self.ids[resource] + ':/tmp/rca-helper'])
        _, receipt = self.command(['exec', '-d', self.ids[resource], '/tmp/rca-helper', '-mode', mode,
                                  '-seconds', str(seconds), '-workers', '8', '-bytes', str(allocation_bytes), '-lease', lease])
        return {**receipt, 'lease': lease, 'helper_sha256': pin['helper_sha256'], 'container_id': self.ids[resource]}


class FaultTransaction:
    """Caller holds the shared lab lease throughout injection and recovery.

    Persist cleanup intent before executing an intervention. Every component
    cleanup is attempted even after another cleanup fails. The block survives
    failures and can be recovered from the same exact identities and leases.
    """
    def __init__(self, driver, journal_path, owner=None):
        self.driver, self.path = driver, Path(journal_path)
        self.owner = owner or str(uuid4())
        self.state = {'schema_version': 3, 'owner': self.owner, 'components': [], 'recovered': False}

    def save(self):
        if self.path.exists() and json.loads(self.path.read_text()).get('owner') != self.owner:
            raise RecoveryFailed('another controller owns the recovery journal')
        atomic_json(self.path, self.state)

    def inject(self, mechanisms):
        if self.path.exists():
            raise RecoveryFailed('an existing transaction blocks injection')
        if type(mechanisms) is not tuple or any(type(p) is not tuple or len(p) != 2 for p in mechanisms):
            raise ValueError('component pairs required')
        causes = cause_set(tuple(c for c, _ in mechanisms))
        for cause, mechanism in mechanisms:
            if mechanism not in MECHANISMS[cause]:
                raise ValueError('unsupported mechanism')
        if causes == PAIR and dict(mechanisms) != {'resource/catalogue-db': 'container_stopped', 'resource/payment': 'cpu_contention'}:
            raise ValueError('unregistered pair mechanisms')
        if any(v != 'running' for v in self.driver.states().values()):
            raise RuntimeError('transaction requires healthy running baseline')
        # CPU starts first so its owned lease exists before stopping the database.
        ordered = sorted(mechanisms, key=lambda row: row[1] != 'cpu_contention')
        try:
            for cause, mechanism in ordered:
                resource = cause.split('/')[1]
                row = {'cause_id': cause, 'resource_id': resource, 'mechanism': mechanism,
                       'container_id': self.driver.ids[resource], 'status': 'INTENT', 'cleanup': None}
                if mechanism in ('cpu_contention', 'memory_pressure'):
                    row['lease'] = str(uuid4())
                    row['expires_at'] = (datetime.now(timezone.utc) + timedelta(seconds=210)).isoformat()
                    if mechanism == 'memory_pressure':
                        row['allocation_bytes'], row['baseline_memory_bytes'] = self.driver.memory_allocation()
                elif mechanism == 'dependency_latency':
                    row['proxy_before'] = self.driver.proxy_control()
                    if row['proxy_before']['delay_ms'] != 0:
                        raise RecoveryFailed('unowned path delay already affects the lab')
                self.state['components'].append(row)
                self.save()
                if mechanism == 'cpu_contention':
                    receipt = self.driver.inject_cpu_lease(row['lease'])
                elif mechanism == 'memory_pressure':
                    receipt = self.driver.inject_resource_lease('carts', 'memory', row['lease'], allocation_bytes=row['allocation_bytes'])
                elif mechanism == 'dependency_latency':
                    receipt = self.driver.proxy_control(800)
                else:
                    receipt = self.driver.inject_stop(resource)
                row.update(status='ACTIVE', injected_at=datetime.now(timezone.utc).isoformat(), injector_receipt=receipt)
                self.save()
            return {row['cause_id']: dict(row) for row in self.state['components']}
        except BaseException:
            self.recover()
            raise

    def recover(self):
        failures = []
        for row in reversed(self.state['components']):
            if isinstance(row.get('cleanup'), dict) and row['cleanup'].get('passed'):
                continue
            try:
                if self.driver.ids[row['resource_id']] != row['container_id']:
                    raise ValueError('recovery container identity changed')
                if row['mechanism'] in ('cpu_contention', 'memory_pressure'):
                    self.driver.helper_leases[row['resource_id']] = row['lease']
                    receipt = self.driver.stop_helper(row['resource_id'])
                elif row['mechanism'] == 'dependency_latency':
                    receipt = self.driver.proxy_control(0)
                else:
                    receipt = self.driver.recover(row['resource_id'])
                row['cleanup'] = {'passed': True, 'receipt': receipt}
            except BaseException as exc:
                row['cleanup'] = {'passed': False, 'error': str(exc)}
                failures.append(row['resource_id'])
            self.save()
        self.state['recovered'] = not failures
        self.save()
        if failures:
            raise RecoveryFailed('component cleanup failed: ' + ', '.join(failures))
        return self.state

    def confirm_recovery(self, workload_check):
        if not self.state['recovered'] or any(v != 'running' for v in self.driver.states().values()) or not workload_check():
            raise RecoveryFailed('component states and workload recovery must pass')
        self.state['recovery_confirmed'] = True
        self.save()
        self.path.unlink()
        return self.state

    @classmethod
    def restore(cls, driver, path):
        state = json.loads(Path(path).read_text())
        if state.get('schema_version') != 3 or not state.get('owner'):
            raise ValueError('invalid recovery journal')
        transaction = cls(driver, path, state['owner'])
        transaction.state = state
        return transaction
