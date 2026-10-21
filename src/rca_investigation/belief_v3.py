"""Set diagnosis over whole-incident prototypes, with explicit class mapping."""
import json
from .belief import PrototypeModel, fit_model
from .contracts import digest
from .diagnosis_sets import cause_set, hypothesis_key, root_marginals


class CauseSetModel:
    def __init__(self, prototype, task_id='sockshop_multicause_v3'):
        self.prototype, self.task_id = prototype, task_id
        self.cause_sets = tuple(cause_set(tuple(json.loads(key)), task_id) for key in prototype.classes)
        if tuple(hypothesis_key(values) for values in self.cause_sets) != prototype.classes:
            raise ValueError('model classes require canonical cause-set mapping')
        self.classes = prototype.classes
        self.action_ids = prototype.action_ids
        self.vocabularies = prototype.vocabularies

    @property
    def hash(self):
        return digest({'schema_version': 3, 'task_id': self.task_id,
                       'prototype_hash': self.prototype.hash, 'cause_sets': self.cause_sets})

    def diagnosis(self, history):
        return self.prototype.diagnosis(history)

    def decode(self, key):
        return self.cause_sets[self.classes.index(key)]

    def posterior_sets(self, history):
        return {self.decode(key): p for key, p in self.diagnosis(history).items()}

    def marginals(self, history):
        return root_marginals(self.posterior_sets(history))

    def predictions(self, history, action):
        return self.prototype.predictions(history, action)

    @property
    def labels(self):
        return self.prototype.labels

    @property
    def responses(self):
        return self.prototype.responses

    @property
    def prior(self):
        return self.prototype.prior

    def to_dict(self):
        from dataclasses import asdict
        return {'schema_version': 3, 'task_id': self.task_id, 'prototype': asdict(self.prototype)}

    @classmethod
    def from_dict(cls, value):
        if set(value) != {'schema_version', 'task_id', 'prototype'} or value['schema_version'] != 3:
            raise ValueError('invalid set model')
        data = value['prototype']
        prototype = PrototypeModel(**{k: tuple(tuple(v) for v in value) if k in ('responses', 'vocabularies')
            else tuple(value) if isinstance(value, list) else value for k, value in data.items()})
        return cls(prototype, value['task_id'])


def fit_set_model(records, hypotheses, specs, smoothing=.1, task_id='sockshop_multicause_v3'):
    converted = [{**record, 'label': hypothesis_key(cause_set(record['label'], task_id))} for record in records]
    classes = tuple(hypothesis_key(cause_set(values, task_id)) for values in hypotheses)
    return CauseSetModel(fit_model(converted, classes, specs, smoothing), task_id)
