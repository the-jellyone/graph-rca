"""Seal a complete coherent cohort without moving preassigned session roles."""
from collections import Counter
from pathlib import Path
import json
from .capture_store import read_raw
from .contracts import digest
from .contracts_v2 import decode_record
from .dataset import atomic_json
from .diagnosis_sets import hypothesis_key
from .schedule_v3 import SCENARIOS
from .topology import sockshop_topology


def split_raw_v3(dataset, schedule_state):
    dataset = Path(dataset)
    state = json.loads(Path(schedule_state).read_text())
    if not state['complete'] or len(state['accepted']) != 440 or state.get('schema_version') != 3:
        raise ValueError('complete 440-incident v3 collection required')
    if Counter(state['roles'].values()) != {'train': 20, 'validation': 10, 'test': 10}:
        raise ValueError('v3 session roles differ from the declaration')
    target = dataset / 'splits.json'
    if target.exists(): raise ValueError('raw v3 split is already sealed')
    splits = {role: [] for role in ('train', 'validation', 'test')}
    hashes, events, counts, strata = {}, set(), {role: Counter() for role in splits}, {role: Counter() for role in splits}
    for slot, identifier in state['accepted'].items():
        payloads, raw_hash = read_raw(dataset / identifier, public_only=False)
        if raw_hash != state['bundle_hashes'][slot] or identifier in hashes:
            raise ValueError('raw slot checksum changed or incident repeated')
        receipt = payloads['capture_receipt.json']
        if receipt['physical_episode'] in events or receipt['status'] != 'ACCEPTED' or not receipt['recovery_confirmed']:
            raise ValueError('invalid independent physical incident')
        events.add(receipt['physical_episode'])
        private = decode_record(payloads['private_label.json'], 'private')
        if getattr(private, 'schema_version', 1) != 3:
            raise ValueError('v3 raw splits cannot mix scalar incident records')
        private.validate_context(sockshop_topology(), 'sockshop_multicause_v3')
        role = state['roles'][receipt['capture_session']]
        if receipt['role'] != role or not slot.startswith(receipt['capture_session']+'/'):
            raise ValueError('capture role or slot changed')
        if receipt['stratum'] not in SCENARIOS or tuple(sorted(private.mechanisms)) != tuple(sorted(SCENARIOS[receipt['stratum']])):
            raise ValueError('stratum/mechanism receipt mismatch')
        splits[role].append(identifier)
        hashes[identifier] = raw_hash
        counts[role][hypothesis_key(private.cause_ids)] += 1
        strata[role][receipt['stratum']] += 1
    for role, number in [('train', 20), ('validation', 10), ('test', 10)]:
        if strata[role] != Counter({name: number for name in SCENARIOS}):
            raise ValueError('primary mechanism strata are incomplete')
    result = {'schema_version': 3, 'group': 'capture_session', 'assignments': state['roles'],
              'splits': {role: sorted(ids) for role, ids in splits.items()}, 'bundle_hashes': hashes,
              'class_counts': {role: dict(v) for role, v in counts.items()},
              'stratum_counts': {role: dict(v) for role, v in strata.items()}, 'schedule_hash': state['schedule_hash']}
    result['hash'] = digest(result)
    atomic_json(target, result)
    return result
