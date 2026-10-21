"""Train-only v3 measurement fitting and one-tool projection of sealed windows."""
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import json
from .contracts import canonical, digest
from .contracts_v2 import VOCABULARIES, UNITS, decode_record
from .dataset import atomic_json
from .metrics.common import covered, percentile
from .metrics.memory import robust_growth
from .operations_v3 import OperationToolV3, OperationResultV3, summarize_requests, OPERATIONS
from .diagnosis_sets import cause_set
from .measurements_v3 import unique_stats


def separation(negative, positive, sessions=5):
    if not negative or not positive or min(len({s for s, _ in negative}), len({s for s, _ in positive})) < sessions:
        raise ValueError('independent healthy and verified fault training bands required')
    upper, lower = max(v for _, v in negative), min(v for _, v in positive)
    if upper >= lower:
        raise ValueError('training separation bands overlap; declare a new evidence revision')
    return {'healthy_upper': upper, 'fault_lower': lower, 'threshold': (upper+lower)/2,
            'healthy_sessions': len({s for s, _ in negative}), 'fault_sessions': len({s for s, _ in positive})}


@dataclass(frozen=True)
class EncoderV3:
    canonical_json: str
    @property
    def values(self): return json.loads(self.canonical_json)
    @property
    def hash(self): return digest(self.values)

    def save(self, path):
        path = Path(path)
        if path.exists():
            if self.load(path).hash != self.hash:
                raise ValueError('v3 fitted encoder is immutable')
            return
        atomic_json(path, {'encoder': self.values, 'hash': self.hash})

    @classmethod
    def load(cls, path):
        payload = json.loads(Path(path).read_text())
        encoder = cls(canonical(payload['encoder']))
        if encoder.hash != payload['hash'] or encoder.values['schema_version'] != 3:
            raise ValueError('v3 encoder identity mismatch')
        return encoder


def fit_encoder(dataset, destination, task_id='sockshop_multicause_v3'):
    from .raw_dataset import load_raw_split
    from .metrics.requests import summarize as errors
    from .logs.parser import summarize as logs
    from .topology import sockshop_topology
    manifest, raw_rows = load_raw_split(dataset, 'train')
    method = json.loads(Path('experiments/v3/encoder-method-declaration.json').read_text())
    records, negative, positive, healthy_operations, peers, growth = [], {}, {}, {}, {}, []
    for identifier, payloads in raw_rows:
        private = decode_record(payloads['private_label.json'], 'private')
        if getattr(private, 'schema_version', 1) != 3:
            raise ValueError('v3 fitting cannot reinterpret scalar raw incidents')
        if task_id == 'sockshop_single_v3' and len(private.cause_ids) == 2:
            continue
        cause_set(private.cause_ids, task_id)
        raw, public, receipt = unique_stats(payloads['measurements.json'], ('payment', 'carts')), payloads['public.json'], payloads['capture_receipt.json']
        if receipt['role'] != 'train' or not receipt['effect_confirmed'] or not receipt['recovery_confirmed']:
            raise ValueError('only independently verified training windows may fit evidence')
        session = receipt['capture_session']
        rows = {}
        for resource in ('payment', 'carts'):
            samples = [r for r in raw['stats'] if r['resource_id'] == resource and r['availability'] == 'OK']
            _, sufficient = covered(samples, public['window_start'], public['window_end'])
            if sufficient: rows[resource] = samples
        values = {}
        if 'payment' in rows:
            values['cpu'] = median(r['cpu_percent']/(100*r['quota_cores']) for r in rows['payment'])
        if 'carts' in rows:
            values['memory'] = median(r['memory_bytes']/r['memory_limit_bytes'] for r in rows['carts'])
            slope, span = robust_growth(rows['carts'])
            if not private.cause_ids and span >= 90: growth.append(slope)
        timings = {}
        for operation in ('checkout', *OPERATIONS):
            requests = [r for r in raw['requests'] if r['operation'] == operation]
            successful = [r['duration_ms'] for r in requests if r['outcome'] == 'completed' and 200 <= r['http_status'] < 300]
            if len(successful) >= 20: timings[operation] = percentile(successful)
            if not private.cause_ids:
                resource = OPERATIONS.get(operation, 'front-end')
                summary = errors([{k: v for k, v in r.items() if k != 'auth_refused'} for r in requests], resource, operation)
                if summary[0] != 'OK' or operation not in timings:
                    raise ValueError('healthy training operation has insufficient coverage')
                healthy_operations.setdefault(operation, []).append({'latency': timings[operation],
                    'errors': summary[2].get('error_fraction', 0.), 'transport': summary[2]['transport_fraction']})
        if not private.cause_ids:
            for family, number in values.items(): negative.setdefault(family, []).append((session, number))
            negative.setdefault('latency', []).append((session, timings['checkout']))
        mechanisms = dict(private.mechanisms)
        if mechanisms.get('resource/payment') == 'cpu_contention':
            if 'cpu' not in values: raise ValueError('verified CPU training window lacks measurement coverage')
            positive.setdefault('cpu', []).append((session, values['cpu']))
        if mechanisms.get('resource/carts') == 'memory_pressure':
            if 'memory' not in values: raise ValueError('verified memory training window lacks coverage')
            positive.setdefault('memory', []).append((session, values['memory']))
        if mechanisms.get('edge/orders/payment') == 'dependency_latency':
            if 'checkout' not in timings: raise ValueError('verified path delay lacks successful timing coverage')
            positive.setdefault('latency', []).append((session, timings['checkout']))
        for resource, log in raw.get('logs', {}).items():
            if log['availability'] == 'OK':
                permitted = [peer for caller, peer in sockshop_topology().calls if caller == resource]
                observed = logs(log['lines'], resource, permitted)[2]['peers']
                peers.setdefault(resource, set()).update(observed)
        records.append(identifier)
    bands = {family: separation(negative.get(family, []), positive.get(family, [])) for family in ('cpu', 'memory', 'latency')}
    operations = {op: {'latency': max(r['latency'] for r in rows), 'request_errors': max(.05, max(r['errors'] for r in rows)),
                       'transport_errors': max(.05, max(r['transport'] for r in rows))} for op, rows in healthy_operations.items()}
    if set(operations) != {'checkout', 'auth', 'shipping_health'}:
        raise ValueError('all declared operations need healthy training support')
    operations['checkout']['latency'] = bands['latency']['threshold']
    thresholds = {'cpu': bands['cpu']['threshold'], 'memory': bands['memory']['threshold'],
                  'memory_growth': max(1048576., max(growth, default=0.)),
                  'latency': operations['checkout']['latency'], 'request_errors': operations['checkout']['request_errors'],
                  'transport_errors': operations['checkout']['transport_errors']}
    encoder = EncoderV3(canonical({'schema_version': 3, 'task_id': task_id, 'training_incidents': sorted(records),
        'split_hash': manifest['hash'], 'method_hash': digest(method), 'bands': bands,
        'thresholds': thresholds, 'thresholds_by_resource': {'cpu': {'payment': thresholds['cpu']},
            'memory': {'carts': thresholds['memory']}, 'memory_growth': {'carts': thresholds['memory_growth']}},
        'operation_thresholds': operations, 'validated_log_peers': {r: sorted(v) for r, v in peers.items()},
        'vocabularies': VOCABULARIES, 'units': UNITS, 'minimum_requests': 20, 'minimum_coverage': .8}))
    encoder.save(destination)
    return encoder


class RawEvidenceBackendV3:
    def __init__(self, specs, raw, topology, encoder):
        from .backends.evidence_v2 import RawEvidenceBackend
        raw = unique_stats(raw, {s.resource_id for s in specs if s.template in ('cpu', 'memory')})
        self.specs, self.raw, self.encoder = tuple(specs), raw, encoder
        self.legacy = RawEvidenceBackend(specs, raw, raw['registry'], topology, encoder)
        self.incident = None

    def begin(self, incident):
        self.incident = incident
        self.legacy.begin(incident)

    def execute(self, spec):
        if not isinstance(spec, OperationToolV3):
            return self.legacy.execute(spec)
        if self.incident is None or spec not in self.specs:
            raise ValueError('unapproved v3 evidence projection')
        from .metrics.latency import summarize as latency
        params = json.loads(spec.parameters_json)
        op = params['operation']
        rows = [r for r in self.raw['requests'] if r['operation'] == op]
        thresholds = self.encoder.values['operation_thresholds'][op]
        summary = summarize_requests(rows, op, thresholds['request_errors'], thresholds['transport_errors']) if spec.template == 'request_errors' else \
            latency(rows, spec.resource_id, op, thresholds['latency'])
        availability, code, evidence, reason = summary
        result = OperationResultV3(spec.action_id, spec.resource_id, availability, code,
            self.incident.window_start, self.incident.window_end, self.incident.window_end, 'workload_operations_v3', 0,
            collected_at=self.raw['collected_at'], encoding_hash=self.encoder.hash, evidence_json=canonical(evidence), reason=reason)
        result.validate_context(spec, self.incident)
        return result

    def close(self):
        self.legacy.close()
        self.incident = None
