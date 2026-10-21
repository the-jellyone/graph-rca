"""Operation-specific v3 evidence. Checkout and container tools retain v2."""
from dataclasses import dataclass
import json
from .contracts import JsonRecord, ToolResult, canonical, identifier, integer, real, unique, timestamp
from .contracts_v2 import VOCABULARIES, object_json, sha256

REQUEST_CODES = ('NORMAL', 'HIGH_HTTP_ERRORS', 'HIGH_TRANSPORT_FAILURES', 'MIXED_FAILURES',
                 'AUTH_REFUSED', 'HIGH_APPLICATION_FAILURES', 'UNAVAILABLE')
OPERATIONS = {'auth': 'front-end', 'shipping_health': 'shipping'}


@dataclass(frozen=True)
class OperationToolV3(JsonRecord):
    action_id: str
    resource_id: str
    template: str
    schema_version: int
    parameters_json: str
    credits: int
    required_capabilities: tuple
    response_vocabulary: tuple
    encoding_hash: str

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 3 or self.template not in ('request_errors', 'latency'):
            raise ValueError('unsupported v3 operation tool')
        params = object_json(self.parameters_json, 2048)
        if set(params) != {'operation', 'min_samples', 'window_seconds'} or params['min_samples'] != 20 or params['window_seconds'] != 120:
            raise ValueError('unsupported operation parameters')
        op = params['operation']
        if op not in OPERATIONS or self.resource_id != OPERATIONS[op] or self.action_id != f'{self.template}/{op}/{self.resource_id}/v3':
            raise ValueError('operation/action identity mismatch')
        integer(self.credits, 1)
        unique(self.required_capabilities)
        if self.required_capabilities != (f'{self.template}/{op}',):
            raise ValueError('operation capability mismatch')
        if self.response_vocabulary != (REQUEST_CODES if self.template == 'request_errors' else VOCABULARIES['latency']):
            raise ValueError('operation vocabulary mismatch')
        sha256(self.encoding_hash)

    @property
    def responses(self):
        return self.response_vocabulary

    @property
    def capabilities(self):
        return self.required_capabilities

    def validate_resource(self, topology):
        if self.resource_id not in topology.resources:
            raise ValueError('operation resource absent')


@dataclass(frozen=True)
class OperationResultV3(ToolResult):
    schema_version: int = 3
    collected_at: str = ''
    encoding_hash: str = ''
    evidence_json: str = '{}'
    reason: str = ''

    def __post_init__(self):
        super().__post_init__()
        if type(self.schema_version) is not int or self.schema_version != 3 or self.raw_json != '{}':
            raise ValueError('invalid v3 operation packet')
        sha256(self.encoding_hash)
        if timestamp(self.collected_at) < timestamp(self.sample_time):
            raise ValueError('collection precedes measurement')
        if type(self.reason) is not str or len(self.reason.encode()) > 256:
            raise ValueError('invalid operation reason')
        parts = self.action_id.split('/')
        if len(parts) != 4 or parts[0] not in ('request_errors', 'latency') or parts[1] not in OPERATIONS or parts[2] != OPERATIONS[parts[1]] or parts[3] != 'v3' or self.resource_id != parts[2]:
            raise ValueError('invalid operation packet identity')
        evidence = object_json(self.evidence_json, 8192)
        if self.availability != 'OK':
            if evidence or not self.reason:
                raise ValueError('unavailable operation needs a reason and no measurements')
            return
        vocab = REQUEST_CODES if parts[0] == 'request_errors' else VOCABULARIES['latency']
        if self.response_code not in vocab or self.response_code == 'UNAVAILABLE':
            raise ValueError('invalid operation response')
        if evidence.get('operation') != parts[1] or evidence.get('resource_id') != self.resource_id:
            raise ValueError('operation measurement identity mismatch')
        fields = {'resource_id', 'operation', 'units', 'sample_count'}
        if parts[0] == 'request_errors':
            fields |= {'completed', 'server_errors', 'attempts', 'semantic_failures', 'transport_failures',
                       'auth_refusals', 'achieved_rate_per_second', 'transport_fraction'}
            if set(evidence) not in (fields, fields | {'error_fraction'}):
                raise ValueError('invalid operation request fields')
            for key in ('sample_count', 'completed', 'server_errors', 'attempts', 'semantic_failures', 'transport_failures', 'auth_refusals'):
                integer(evidence[key])
            n, completed = evidence['attempts'], evidence['completed']
            if n < 20 or n != completed + evidence['transport_failures'] or evidence['sample_count'] != n:
                raise ValueError('invalid operation denominator')
            if evidence['units'] != 'fraction' or evidence['server_errors'] + evidence['semantic_failures'] > completed or evidence['auth_refusals'] > evidence['semantic_failures']:
                raise ValueError('invalid request counts or units')
            if parts[1] != 'auth' and evidence['auth_refusals']:
                raise ValueError('auth refusal in unrelated operation')
            real(evidence['achieved_rate_per_second'])
            real(evidence['transport_fraction'], 0, 1)
            if abs(evidence['transport_fraction'] - evidence['transport_failures']/n) > 1e-9:
                raise ValueError('transport fraction mismatch')
            if completed >= 20:
                real(evidence.get('error_fraction'), 0, 1)
                if abs(evidence['error_fraction'] - evidence['server_errors']/completed) > 1e-9:
                    raise ValueError('HTTP fraction mismatch')
            elif 'error_fraction' in evidence:
                raise ValueError('insufficient completed denominator')
        else:
            fields |= {'median_ms', 'p95_ms', 'timeout_count', 'failed_count', 'coverage'}
            if set(evidence) != fields or evidence['units'] != 'milliseconds':
                raise ValueError('invalid operation latency fields')
            integer(evidence['sample_count'], 20)
            for key in ('timeout_count', 'failed_count'):
                integer(evidence[key])
            for key in ('median_ms', 'p95_ms'):
                real(evidence[key])
            real(evidence['coverage'], 0, 1)
            expected = evidence['sample_count']/(evidence['sample_count'] + evidence['failed_count'])
            if evidence['timeout_count'] > evidence['failed_count'] or abs(evidence['coverage'] - expected) > 1e-9:
                raise ValueError('latency coverage mismatch')

    def validate_context(self, spec, incident):
        super().validate_context(spec, incident)
        if not isinstance(spec, OperationToolV3) or self.encoding_hash != spec.encoding_hash or self.encoding_hash != incident.encoding_hash:
            raise ValueError('operation encoder mismatch')


def summarize_requests(rows, operation, threshold=.05, transport_threshold=.05):
    from .metrics.requests import summarize
    resource = OPERATIONS[operation]
    base_rows = [{k: v for k, v in row.items() if k != 'auth_refused'} for row in rows]
    availability, code, evidence, reason = summarize(base_rows, resource, operation, threshold=threshold, transport_threshold=transport_threshold)
    if availability != 'OK':
        return availability, code, evidence, reason
    refusals = sum(row.get('auth_refused', False) for row in rows)
    evidence['auth_refusals'] = refusals
    if code == 'NORMAL' and refusals / evidence['attempts'] > threshold:
        code = 'AUTH_REFUSED'
    elif code == 'NORMAL' and evidence['semantic_failures'] / evidence['attempts'] > threshold:
        code = 'HIGH_APPLICATION_FAILURES'
    return availability, code, evidence, reason
