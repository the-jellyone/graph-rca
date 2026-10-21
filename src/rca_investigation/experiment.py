"""Frozen dataset and model loading shared by training and evaluation."""
from dataclasses import asdict
import json
from pathlib import Path
from .belief import PrototypeModel, fit_model
from .dataset import atomic_json, read_split, load_public_bundle, load_private_label
from .backends.replay import ReplayBackend
from .environment import InvestigationEnv


def label_name(label):
    if getattr(label, 'schema_version', 1) == 3:
        return label.cause_ids
    from .contracts_v2 import IncidentPrivateV2
    if isinstance(label, IncidentPrivateV2):
        return label.cause_id
    if len(label.resources)>1:
        raise ValueError('single-root task cannot score a multi-root label')
    return label.resources[0] if label.resources else 'NO_FAULT'


def fit_dataset(dataset, config, specs, destination):
    manifest, paths = read_split(dataset,'train')
    records = []
    for path in paths:
        public,_,packets = load_public_bundle(path,specs)
        records.append({'split':'train','incident_id':public.incident_id,'label':label_name(load_private_label(path)),
                        'packets':{p.action_id:p for p in packets}})
    if config.values['schema_version'] == 3:
        from .belief_v3 import fit_set_model
        model = fit_set_model(records, tuple(tuple(v) for v in config.values['diagnoses']), specs,
                              config.values['model']['smoothing'], config.values['task_id'])
    else:
        model = fit_model(records,config.values['diagnoses'],specs,config.values['model']['smoothing'])
    payload = {'model':model.to_dict() if config.values['schema_version'] == 3 else asdict(model),'model_hash':model.hash,'config_hash':config.hash,
               'split_hash':manifest['hash'],'training_incidents':len(paths)}
    if config.values['schema_version'] in (2, 3) and Path(destination).exists():
        from .contracts import digest
        if digest(json.loads(Path(destination).read_text(encoding='utf-8'))) != digest(payload):
            raise ValueError('fitted v2 model is immutable; declare a new artifact path')
        return model
    atomic_json(destination,payload)
    return model


def load_model(path, config, specs, split_hash=None):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    data = payload['model']
    if config.values['schema_version'] == 3:
        from .belief_v3 import CauseSetModel
        model = CauseSetModel.from_dict(data)
    else:
        model = PrototypeModel(**{k:tuple(tuple(v) for v in value) if k in ('responses','vocabularies')
                                  else tuple(value) if isinstance(value,list) else value for k,value in data.items()})
    if model.hash != payload['model_hash'] or config.hash != payload['config_hash']:
        raise ValueError('model or task checksum mismatch')
    if tuple(s.action_id for s in specs) != model.action_ids or split_hash is not None and split_hash != payload['split_hash']:
        raise ValueError('model catalogue or frozen split mismatch')
    return model


def replay_environment(path, specs, model, config):
    public,topology,packets = load_public_bundle(path,specs)
    objective=config.values['objective']
    if getattr(public, 'schema_version', 1) in (2, 3):
        from .contracts import digest
        objective_record = objective
        if public.schema_version == 3:
            from .contracts_v3 import ObjectiveV3
            objective_record = ObjectiveV3(**objective).to_dict()
        if public.task_id != config.values['task_id'] or public.encoding_hash != config.values['encoding_hash'] or public.objective_hash != digest(objective_record):
            raise ValueError('replay task, encoder or objective mismatch')
    env = InvestigationEnv(topology,specs,model,ReplayBackend(packets,specs),
        defer_utility=objective['defer'],correct_utility=objective['correct'],incorrect_utility=objective['incorrect'])
    env.reset(public)
    return env
