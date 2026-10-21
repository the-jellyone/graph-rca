"""Live short-window packet validation; frozen benchmark packets are unchanged.

V2 field checks are retained. Container sample support scales with the actual
observation duration, in addition to the live CPU and memory readiness gates.
No sample counts or timestamps are padded. Never write benchmark bundles with
this live-only packet class.
"""
import json
import math
from rca_investigation.contracts import ToolResult, timestamp, integer, real, identifier, canonical
from rca_investigation.contracts_v2 import ToolResultV2, sha256, object_json, UNITS


class ShortPacket(ToolResultV2):
    def __post_init__(self):
        ToolResult.__post_init__(self)
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError('unsupported packet schema')
        if timestamp(self.collected_at) < timestamp(self.sample_time):
            raise ValueError('collection precedes evidence')
        sha256(self.encoding_hash)
        if type(self.reason) is not str or len(self.reason.encode()) > 256:
            raise ValueError('invalid bounded reason')
        if self.availability == 'OK' and self.response_code == 'UNAVAILABLE':
            raise ValueError('OK cannot be unavailable')
        if self.availability != 'OK' and not self.reason:
            raise ValueError('unavailable evidence requires a reason')
        evidence = object_json(self.evidence_json, 8192)
        template = self.action_id.split('/')[0]
        if template not in UNITS or self.action_id != f'{template}/{self.resource_id}/v2':
            raise ValueError('invalid packet action')
        # Raw measurements belong in the raw capture store, never paid packets.
        if self.raw_json != '{}':
            raise ValueError('v2 packets cannot expose raw collector payloads')
        if self.availability == 'OK':
            if evidence.get('units') != UNITS[template]:
                raise ValueError('measurement units mismatch')
            integer(evidence.get('sample_count'), 0 if template == 'logs' else 1)
            if evidence.get('resource_id') != self.resource_id:
                raise ValueError('measurement resource mismatch')
            allowed = {
                'status': {'state'},
                'cpu': {'median', 'p95', 'high_fraction', 'coverage', 'quota_cores'},
                'memory': {'median_bytes', 'p95_bytes', 'limit_bytes', 'limit_fraction', 'peak_fraction', 'growth_bytes_per_second', 'growth_span_seconds', 'coverage'},
                'request_errors': {'operation', 'completed', 'server_errors', 'error_fraction', 'transport_failures', 'attempts', 'semantic_failures', 'transport_fraction', 'achieved_rate_per_second'},
                'latency': {'operation', 'median_ms', 'p95_ms', 'timeout_count', 'failed_count', 'coverage'},
                'logs': {'peers', 'event_counts', 'truncated', 'lines', 'warnings', 'max_lines', 'max_bytes'},
            }[template] | {'units', 'sample_count', 'resource_id'}
            if set(evidence) - allowed:
                raise ValueError('packet reveals undeclared evidence fields')
            required = {
                'status': {'state'},
                'cpu': {'median', 'p95', 'high_fraction', 'coverage', 'quota_cores'},
                'memory': {'median_bytes', 'p95_bytes', 'limit_bytes', 'limit_fraction', 'peak_fraction', 'growth_bytes_per_second', 'growth_span_seconds', 'coverage'},
                'request_errors': {'operation', 'completed', 'server_errors', 'transport_failures', 'attempts', 'semantic_failures', 'achieved_rate_per_second'},
                'latency': {'operation', 'median_ms', 'p95_ms', 'timeout_count', 'failed_count', 'coverage'},
                'logs': {'peers', 'event_counts', 'truncated', 'lines', 'warnings', 'max_lines', 'max_bytes'},
            }[template]
            if not required <= set(evidence):
                raise ValueError('packet omits required measurement fields')
            fractions = {'high_fraction', 'coverage', 'limit_fraction', 'peak_fraction', 'error_fraction', 'transport_fraction'}
            integers = {'median_bytes', 'p95_bytes', 'limit_bytes', 'completed', 'server_errors', 'transport_failures', 'timeout_count', 'attempts', 'semantic_failures', 'failed_count', 'max_lines', 'max_bytes'}
            for name in set(evidence) - {'units', 'sample_count', 'resource_id', 'state', 'operation', 'peers', 'event_counts', 'truncated', 'lines', 'warnings'}:
                if name in integers:
                    integer(evidence[name], 1 if name == 'limit_bytes' else 0)
                else:
                    real(evidence[name], 0, 1 if name in fractions - {'limit_fraction', 'peak_fraction'} else None)
            if 'quota_cores' in evidence and evidence['quota_cores'] == 0:
                raise ValueError('CPU quota must be positive')
            if template in {'cpu', 'memory'} and (evidence['coverage'] < .8 or evidence['sample_count'] < max(2, math.ceil((timestamp(self.window_end)-timestamp(self.window_start)).total_seconds()*.8/2))):
                raise ValueError('insufficient container measurement coverage')
            if template == 'latency' and evidence['sample_count'] < 20:
                raise ValueError('insufficient successful latency samples')
            if template == 'status':
                state = evidence['state']
                expected = 'RUNNING' if state == 'running' else 'STOPPED' if state in {'exited', 'dead'} else 'OTHER_STATE'
                if type(state) is not str or self.response_code != expected:
                    raise ValueError('state and response disagree')
            if template in {'request_errors', 'latency'} and evidence.get('operation') != 'checkout':
                raise ValueError('measurement operation mismatch')
            if template == 'request_errors' and ('completed' not in evidence or
                    evidence.get('server_errors', 0) > evidence['completed']):
                raise ValueError('invalid completed request denominator')
            if template == 'request_errors':
                attempts = evidence.get('attempts', evidence['completed'] + evidence.get('transport_failures', 0))
                if attempts != evidence['completed'] + evidence.get('transport_failures', 0):
                    raise ValueError('invalid request attempt denominator')
                if 'error_fraction' in evidence and evidence['completed'] < 20:
                    raise ValueError('insufficient HTTP denominator')
                if 'transport_fraction' in evidence and attempts < 20:
                    raise ValueError('insufficient transport denominator')
                if evidence['semantic_failures'] > evidence['completed'] or attempts < 20:
                    raise ValueError('invalid request counts')
                if evidence['completed'] >= 20 and 'error_fraction' not in evidence or 'transport_fraction' not in evidence:
                    raise ValueError('request fraction missing for valid denominator')
                if 'error_fraction' in evidence and abs(evidence['error_fraction'] - evidence['server_errors'] / evidence['completed']) > 1e-9:
                    raise ValueError('HTTP fraction disagrees with counts')
                if abs(evidence['transport_fraction'] - evidence['transport_failures'] / attempts) > 1e-9:
                    raise ValueError('transport fraction disagrees with counts')
            if template == 'logs':
                peers = evidence.get('peers', [])
                if type(peers) is not list or len(peers) != len(set(peers)):
                    raise ValueError('invalid log peers')
                for peer in peers:
                    identifier(peer)
                counts = evidence.get('event_counts', {})
                if type(counts) is not dict:
                    raise ValueError('invalid event counts')
                for key, count in counts.items():
                    identifier(key)
                    integer(count)
                if 'truncated' in evidence and type(evidence['truncated']) is not bool:
                    raise ValueError('invalid truncation flag')
                for field in ('lines', 'warnings'):
                    values = evidence.get(field, [])
                    if type(values) is not list or len(values) > 20 or any(type(v) is not str for v in values):
                        raise ValueError('invalid bounded log text')
                if len(canonical(evidence.get('lines', [])).encode()) > 7000:
                    raise ValueError('log explanation exceeds packet bound')
                if evidence['max_lines'] != 20 or evidence['max_bytes'] != 8192 or len(evidence['lines']) != evidence['sample_count']:
                    raise ValueError('log bounds or sample count changed')
        elif evidence:
            raise ValueError('unavailable packet cannot reveal measurements')
