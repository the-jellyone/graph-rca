"""Separate single/multi views and per-stratum full-evidence validation gates."""
from dataclasses import replace
from collections import Counter
from pathlib import Path
import json
from .contracts import digest, canonical
from .contracts_v2 import decode_record
from .contracts_v3 import IncidentPublicV3, DecisionV3, ObjectiveV3
from .dataset import atomic_json, verify_bundle, read_split, load_public_bundle, load_private_label, write_bundle
from .diagnosis_sets import hypothesis_key, cause_set, root_metrics
from .encoding_v3 import EncoderV3, RawEvidenceBackendV3
from .raw_dataset import load_raw_split
from .catalogue import catalogue_hash
from .experiment import load_model
from .train import runtime_hash


def encode_dataset(config, topology, specs):
    values, paths = config.values, config.values['paths']
    encoder = EncoderV3.load(paths['encoder'])
    if encoder.hash != values['encoding_hash'] or encoder.values['task_id'] != values['task_id']:
        raise ValueError('v3 encoder/task identity mismatch')
    raw_manifest, _ = load_raw_split(paths['raw_dataset'], 'train')
    if encoder.values['split_hash'] != raw_manifest['hash']:
        raise ValueError('v3 encoder was fitted on another split')
    target = Path(paths['dataset'])
    if (target / 'splits.json').exists(): raise ValueError('v3 derivation is already sealed')
    identity = {'schema_version': 3, 'raw_split_hash': raw_manifest['hash'], 'config_hash': config.hash,
                'encoder_hash': encoder.hash, 'catalogue_hash': catalogue_hash(specs), 'runtime_hash': runtime_hash()}
    if (target / 'derivation.json').exists():
        if json.loads((target / 'derivation.json').read_text()) != identity:
            raise ValueError('v3 partial derivation identity changed')
    else:
        atomic_json(target / 'derivation.json', identity)
    splits, hashes = {role: [] for role in ('train', 'validation', 'test')}, {}
    counts, strata = {role: Counter() for role in splits}, {role: Counter() for role in splits}
    for role in splits:
        _, rows = load_raw_split(paths['raw_dataset'], role)
        for identifier, payloads in rows:
            private = decode_record(payloads['private_label.json'], 'private')
            if values['task_id'] == 'sockshop_single_v3' and len(private.cause_ids) == 2: continue
            cause_set(private.cause_ids, values['task_id'])
            private = replace(private, task_id=values['task_id'])
            raw_public, raw = payloads['public.json'], payloads['measurements.json']
            if digest(raw['registry']) != raw_public['registry_hash']:
                raise ValueError('raw registry checksum mismatch')
            public = IncidentPublicV3(identifier, topology.hash, catalogue_hash(specs), 'front-end', 'manual_frontend',
                raw_public['window_start'], raw_public['window_end'], values['episode']['budget'], values['episode']['max_probes'],
                values['objective']['lambda_cost'], task_id=values['task_id'], encoding_hash=encoder.hash,
                objective_hash=digest(ObjectiveV3(**values['objective']).to_dict()))
            receipt = {**payloads['capture_receipt.json'], 'raw_hash': raw_manifest['bundle_hashes'][identifier],
                       'encoder_hash': encoder.hash, 'catalogue_hash': catalogue_hash(specs), 'task_id': values['task_id']}
            if (target / identifier).exists():
                saved = json.loads((target / identifier / 'capture_receipt.json').read_text())
                if saved != receipt: raise ValueError('existing v3 derived receipt changed')
            else:
                backend = RawEvidenceBackendV3(specs, raw, topology, encoder)
                backend.begin(public)
                packets = tuple(backend.execute(spec) for spec in specs)
                backend.close()
                write_bundle(target, public, topology, packets, private, receipt)
            hashes[identifier] = verify_bundle(target / identifier)
            splits[role].append(identifier)
            counts[role][hypothesis_key(private.cause_ids)] += 1
            strata[role][receipt['stratum']] += 1
    if set(encoder.values['training_incidents']) != set(splits['train']):
        raise ValueError('encoder training IDs differ from the declared task scope')
    result = {'schema_version': 3, 'group': 'capture_session', 'assignments': raw_manifest['assignments'],
              'splits': splits, 'bundle_hashes': hashes, 'class_counts': {r: dict(v) for r, v in counts.items()},
              'stratum_counts': {r: dict(v) for r, v in strata.items()}, **identity}
    result['hash'] = digest(result)
    atomic_json(target / 'splits.json', result)
    return {'derived_incidents': len(hashes), 'derived_split_hash': result['hash']}


def full_evidence_reference(config, specs):
    paths = config.values['paths']
    manifest, _ = read_split(paths['dataset'], 'train')
    _, validation = read_split(paths['dataset'], 'validation')
    model = load_model(paths['model'], config, specs, manifest['hash'])
    outcomes, strata = [], {}
    for path in validation:
        public, _, packets = load_public_bundle(path, specs)
        probabilities = model.diagnosis(tuple((p.action_id, p.symbol) for p in packets))
        key = max(model.classes, key=lambda c: probabilities[c])
        truth = load_private_label(path).cause_ids
        receipt = json.loads((path / 'capture_receipt.json').read_text())
        decision = DecisionV3(3, 'DIAGNOSE', model.decode(key), probabilities[key], tuple(p.evidence_id for p in packets),
                              digest('full_evidence_reference_v3'), model.hash, 'full_evidence_reference')
        metrics = root_metrics(decision, truth)
        row = {'incident_id': public.incident_id, 'truth': truth, 'prediction': decision.cause_ids,
               'stratum': receipt['stratum'], 'correct': metrics['exact'], 'confidence': decision.confidence,
               'root_metrics': metrics, 'brier': sum((p-(c == hypothesis_key(truth)))**2 for c, p in probabilities.items())}
        outcomes.append(row)
        strata.setdefault(receipt['stratum'], []).append(row)
    counts = {name: {'correct': sum(row['correct'] for row in rows), 'count': len(rows),
                     'accuracy': sum(row['correct'] for row in rows)/len(rows)} for name, rows in strata.items()}
    accuracy = sum(row['correct'] for row in outcomes)/len(outcomes)
    healthy = counts.get('healthy', {'accuracy': 0})['accuracy']
    passed = accuracy >= .9 and healthy >= .9 and all(v['accuracy'] >= .9 for v in counts.values())
    return {'passed': True, 'training_promotion_passed': passed, 'diagnostic_upper_reference_only': True,
            'limited_budget_policy': False, 'config_hash': config.hash, 'model_hash': model.hash, 'split_hash': manifest['hash'],
            'accuracy': accuracy, 'healthy_specificity': healthy, 'strata': counts, 'outcomes': outcomes,
            'mean_brier': sum(row['brier'] for row in outcomes)/len(outcomes), 'reserved_test_opened': False,
            'fitting_partition': 'train', 'configured_smoothing': config.values['model']['smoothing']}
