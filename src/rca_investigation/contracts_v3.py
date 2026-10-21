"""V3 incident and decision records. Evidence semantics remain explicitly v2."""
from dataclasses import dataclass
from .contracts import JsonRecord, IncidentPublic, identifier, unique, real, timestamp
from .contracts_v2 import object_json, sha256, ToolSpecV2
from .diagnosis_sets import cause_set, PROFILES

MECHANISMS = {cause: ('container_stopped',) for cause in (
    'resource/catalogue-db', 'resource/orders', 'resource/orders-db', 'resource/user', 'resource/shipping')}
MECHANISMS.update({'resource/payment': ('cpu_contention', 'container_stopped'),
                   'resource/carts': ('memory_pressure',), 'edge/orders/payment': ('dependency_latency',)})


@dataclass(frozen=True)
class ObjectiveV3(JsonRecord):
    schema_version: int = 3
    lambda_cost: float = .05
    correct: float = 1.
    incorrect: float = 0.
    defer: float = .25

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ValueError('unsupported objective schema')
        for value in (self.lambda_cost, self.correct, self.incorrect, self.defer):
            real(value)
        for name in ('lambda_cost', 'correct', 'incorrect', 'defer'):
            object.__setattr__(self, name, float(getattr(self, name)))
        if not self.incorrect <= self.defer <= self.correct:
            raise ValueError('invalid terminal utilities')


@dataclass(frozen=True)
class IncidentPublicV3(IncidentPublic):
    schema_version: int = 3
    task_id: str = 'sockshop_multicause_v3'
    encoding_hash: str = ''
    objective_hash: str = ''
    alert_context_json: str = '{}'

    def __post_init__(self):
        super().__post_init__()
        if type(self.schema_version) is not int or self.schema_version != 3 or self.task_id not in PROFILES:
            raise ValueError('unsupported v3 incident')
        sha256(self.encoding_hash)
        sha256(self.objective_hash)
        if self.entry_resource != 'front-end' or object_json(self.alert_context_json) != {}:
            raise ValueError('v3 requires the constant manual frontend entry')
        if (timestamp(self.window_end) - timestamp(self.window_start)).total_seconds() != 120:
            raise ValueError('v3 requires a 120 second observation')

    def validate_context(self, topology, catalogue):
        super().validate_context(topology, catalogue)
        from .operations_v3 import OperationToolV3
        if any(not isinstance(s, (ToolSpecV2, OperationToolV3)) or s.encoding_hash != self.encoding_hash for s in catalogue):
            raise ValueError('v3 evidence catalogue or encoder mismatch')


@dataclass(frozen=True)
class IncidentPrivateV3(JsonRecord):
    schema_version: int
    incident_id: str
    task_id: str
    cause_ids: tuple
    mechanisms: tuple
    component_receipts_json: str
    verification_json: str
    recovery_json: str

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ValueError('unsupported private schema')
        identifier(self.incident_id)
        object.__setattr__(self, 'cause_ids', cause_set(self.cause_ids, self.task_id))
        if type(self.mechanisms) is not tuple or any(type(v) is not tuple or len(v) != 2 for v in self.mechanisms):
            raise ValueError('mechanisms require cause/mechanism pairs')
        if len(self.mechanisms) != len(self.cause_ids) or {v[0] for v in self.mechanisms} != set(self.cause_ids):
            raise ValueError('exactly one mechanism per active cause required')
        for cause, mechanism in self.mechanisms:
            if mechanism not in MECHANISMS[cause]:
                raise ValueError('contradictory or unsupported mechanism')
        if len(self.cause_ids) == 2 and dict(self.mechanisms) != {
                'resource/catalogue-db': 'container_stopped', 'resource/payment': 'cpu_contention'}:
            raise ValueError('pair mechanism mixture outside declaration')
        object.__setattr__(self, 'mechanisms', tuple(sorted(self.mechanisms)))
        receipts = object_json(self.component_receipts_json)
        if set(receipts) != set(self.cause_ids):
            raise ValueError('one receipt per active root required')
        resources = []
        for cause, mechanism in self.mechanisms:
            receipt = receipts[cause]
            if type(receipt) is not dict or receipt.get('cause_id') != cause or receipt.get('mechanism') != mechanism:
                raise ValueError('component receipt mismatch')
            resource = cause.split('/')[1]
            if receipt.get('resource_id') != resource:
                raise ValueError('component resource mismatch')
            resources.append(resource)
        unique(tuple(resources))
        object_json(self.verification_json)
        object_json(self.recovery_json)

    def validate_context(self, topology, task_id):
        if task_id != self.task_id:
            raise ValueError('private task mismatch')
        for cause in self.cause_ids:
            parts = cause.split('/')
            if parts[0] == 'resource' and parts[1] not in topology.resources or parts[0] == 'edge' and tuple(parts[1:]) not in topology.calls:
                raise ValueError('cause outside topology')


@dataclass(frozen=True)
class DecisionV3(JsonRecord):
    schema_version: int
    decision_type: str
    cause_ids: tuple
    confidence: float
    evidence_ids: tuple
    policy_hash: str
    model_hash: str
    stop_reason: str

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 3 or self.decision_type not in ('DIAGNOSE', 'DEFER'):
            raise ValueError('invalid v3 decision type')
        object.__setattr__(self, 'cause_ids', cause_set(self.cause_ids))
        real(self.confidence, 0, 1)
        unique(self.evidence_ids)
        sha256(self.policy_hash)
        sha256(self.model_hash)
        identifier(self.stop_reason)
        if self.decision_type == 'DEFER' and (self.cause_ids or self.confidence):
            raise ValueError('DEFER carries no predicted roots or confidence')
