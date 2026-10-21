"""Profile-aware adaptive commands with no v1 artifact path defaults."""
import argparse
from pathlib import Path
import json
import subprocess
from .contracts import digest
from .dataset import atomic_json
from .topology import sockshop_topology
from .config import load_config
from .catalogue import build_catalogue, catalogue_hash


COMMANDS = {'validate-profile', 'verify-profile', 'validate-adaptive-fixtures', 'validate-multicause-fixtures', 'capture-raw',
            'split-raw', 'fit-encoder', 'encode-dataset', 'full-evidence-reference', 'declare-ablations', 'declare-cost-sweep'}


def main(argv):
    parser = argparse.ArgumentParser(description='Adaptive v2 profile checks')
    parser.add_argument('command', choices=sorted(COMMANDS))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--schedule', type=Path)
    parser.add_argument('--schedule-state', type=Path)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--encoder', type=Path)
    parser.add_argument('--split', choices=['train', 'validation', 'test'], default='train')
    args = parser.parse_args(argv)
    destination = args.output or Path('runs/adaptive_v3_multi' if args.command == 'validate-multicause-fixtures' else 'runs/adaptive_v2') / (args.command + '.json')
    try:
        if args.command == 'validate-multicause-fixtures':
            from .multicause_fixtures import validate_multicause_contracts, validate_decision_fixtures
            report = validate_multicause_contracts()
            report['decision_fixtures'] = validate_decision_fixtures()
            report['passed'] &= report['decision_fixtures']['passed']
        elif args.command == 'validate-adaptive-fixtures':
            from .adaptive_fixtures import validate_adaptive_fixtures
            report = validate_adaptive_fixtures()
        elif args.command == 'split-raw':
            from .raw_dataset import split_raw
            if args.dataset is None or args.schedule_state is None:
                raise ValueError('split-raw requires dataset and frozen schedule state')
            report = {'passed': True, 'split': split_raw(args.dataset, args.schedule_state)}
        elif args.command == 'fit-encoder':
            from .raw_dataset import fit_raw_encoder
            if args.split != 'train' or args.dataset is None:
                raise ValueError('fit-encoder accepts frozen training raw data only')
            if args.encoder is None:
                if args.config is None:
                    raise ValueError('declare --encoder or a profile config')
                args.encoder = Path(load_config(args.config, sockshop_topology()).values['paths']['encoder'])
            if args.config is not None:
                config = load_config(args.config, sockshop_topology())
            else:
                config = None
            if config is not None and config.values['schema_version'] == 3:
                from .encoding_v3 import fit_encoder
                encoder = fit_encoder(args.dataset, args.encoder, config.values['task_id'])
            else:
                raw_manifest = json.loads((args.dataset / 'splits.json').read_text())
                if raw_manifest.get('schema_version') == 3:
                    raise ValueError('v3 encoder fitting requires an explicit task config')
                encoder = fit_raw_encoder(args.dataset, args.encoder)
            report = {'passed': True, 'encoder_hash': encoder.hash, 'encoder': str(args.encoder)}
        else:
            if args.config is None:
                raise ValueError('adaptive profile requires --config')
            topology = sockshop_topology()
            config = load_config(args.config, topology)
            if config.values['schema_version'] not in (2, 3):
                raise ValueError('adaptive command requires v2 or v3 config')
            if config.values['schema_version'] == 3 and args.command not in ('validate-profile', 'verify-profile', 'capture-raw', 'encode-dataset', 'full-evidence-reference'):
                raise ValueError(f'v3 {args.command} is not yet implemented; no v2 fallback is permitted')
            specs = build_catalogue(config, topology)
            report = {'passed': True, 'config_hash': config.hash, 'catalogue_hash': catalogue_hash(specs),
                      'topology_hash': topology.hash, 'paths': config.values['paths'], 'actions': [s.action_id for s in specs],
                      'encoder_fitted': config.values['encoding_hash'] != '0' * 64}
            destination = args.output or Path(config.values['paths']['runs']) / (args.command + '.json')
            if args.command == 'verify-profile':
                if config.values['schema_version'] == 3:
                    import yaml
                    from .faults_v3 import LabDriverV3
                    registry = yaml.safe_load(Path(config.values['paths']['resource_registry']).read_text())
                    claimed = registry.pop('hash')
                    if digest(registry) != claimed:
                        raise ValueError('v3 registry checksum mismatch')
                    registry['hash'] = claimed
                    driver = LabDriverV3(topology, Path.cwd(), registry['resources'])
                    driver.resolve()
                    if any(state != 'running' for state in driver.states().values()):
                        raise ValueError('v3 live registry has stopped resources')
                else:
                    from .capabilities import inventory, save_inventory
                    registry = inventory(Path.cwd())
                    save_inventory(Path.cwd(), registry)
                missing = [s.action_id for s in specs if not set(s.capabilities) <= set(registry['resources'].get(s.resource_id, {}).get('capabilities', []))]
                report.update(passed=registry['passed'] and not missing, missing_capabilities=missing, inventory_hash=registry['hash'])
            elif args.command == 'capture-raw':
                if args.schedule is None:
                    raise ValueError('capture-raw requires a declared schedule')
                if config.values['schema_version'] == 3:
                    from .raw_schedule_v3 import capture_raw_v3
                    state = capture_raw_v3(Path.cwd(), args.config, args.schedule)
                else:
                    from .raw_schedule import capture_raw
                    state = capture_raw(Path.cwd(), args.config, args.schedule)
                report.update(captures_accepted=len(state['accepted']), complete=state['complete'])
            elif args.command == 'encode-dataset':
                from .raw_dataset import load_raw_split
                if config.values['schema_version'] == 3:
                    from .derived_dataset_v3 import encode_dataset
                else:
                    from .derived_dataset import encode_dataset
                report.update(encode_dataset(config, topology, specs))
            elif args.command == 'full-evidence-reference':
                if config.values['schema_version'] == 3:
                    from .derived_dataset_v3 import full_evidence_reference
                else:
                    from .derived_dataset import full_evidence_reference
                if args.split != 'validation':
                    raise ValueError('full evidence reference is a validation check before selection')
                report.update(full_evidence_reference(config, specs))
            elif args.command == 'declare-ablations':
                from .ablations import declare_ablations
                if args.output is None:
                    raise ValueError('declare-ablations requires an explicit new directory in --output')
                report.update(declare_ablations(config, topology, specs, args.output))
                destination = args.output / 'declaration-report.json'
            elif args.command == 'declare-cost-sweep':
                from .ablations import declare_cost_sweep
                if args.output is None:
                    raise ValueError('declare-cost-sweep requires an explicit new directory in --output')
                report.update(declare_cost_sweep(config, topology, args.output))
                destination = args.output / 'declaration-report.json'
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        report = {'passed': False, 'error': str(exc), 'error_type': type(exc).__name__}
    atomic_json(destination, report)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1
