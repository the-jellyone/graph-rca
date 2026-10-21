"""Portable, checksummed inference export. Never fits or selects from test data."""
import hashlib
import json
import platform
import shutil
from pathlib import Path, PureWindowsPath
from rca_investigation.contracts import digest, canonical
from rca_investigation.dataset import atomic_json
from rca_investigation.config import TaskConfig, load_config
from rca_investigation.topology import sockshop_topology
from rca_investigation.catalogue import build_catalogue
from rca_investigation.encoding_v3 import EncoderV3
from rca_investigation.experiment import load_model

POLICIES = ('dqn','dp','voi','expected-sarsa','two-step-sarsa',
            'expected-sarsa-hybrid','two-step-sarsa-hybrid','random-valid','expanded-rule')

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def checked_record(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if value['hash'] != digest({k:v for k,v in value.items() if k!='hash'}):
        raise ValueError('record checksum changed: '+str(path))
    return value

def export_bundle(root, destination):
    root,destination=Path(root).resolve(),Path(destination).resolve()
    if destination.exists():
        raise ValueError('export destination already exists; preserve the previous bundle')
    frozen=checked_record(root/'runs/adaptive_v3_multi/final.json')
    source=checked_record(root/'experiments/v3/final-runtime-source.json')
    if source['runtime_hash'] != frozen['runtime_hash']:
        raise ValueError('source does not match frozen comparison')
    config=load_config(root/'configs/investigation_v3_multi.yaml',sockshop_topology())
    if config.hash!=frozen['config_hash']:
        raise ValueError('resolved config changed')
    destination.mkdir(parents=True)
    files={}
    def copy(src, name):
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,target)
        files[name]=sha(target)
    copy(root/'runs/adaptive_v3_multi/final.json','frozen.json')
    copy(root/'experiments/v3/final-runtime-source.json','source.json')
    copy(root/config.values['paths']['encoder'],'encoder.json')
    copy(root/'runs/adaptive_v3_multi/model.json','model.json')
    copy(root/'configs/resources_sockshop_v3.yaml','training-registry.yaml')
    copy(root/'configs/lab_adaptive_v2_lease.json','helper-pin.json')
    copy(root/'lab/adaptive-helper/rca-helper-lease','helper')
    copy(root/'experiments/v3/neural/uv.lock','neural.lock')
    copy(root/'uv.lock','core.lock')
    atomic_json(destination/'config.json',config.values)
    files['config.json']=sha(destination/'config.json')
    representatives={**frozen['representatives'],'dp':'dp','voi':'voi',
                     'random-valid':'random-valid-11','expanded-rule':'expanded-rule'}
    definitions={}
    for name, key in representatives.items():
        definition=dict(frozen['definitions'][key])
        if 'checkpoint' in definition:
            original=definition['checkpoint']
            # Export on the owner host only. Relocation happens in this new manifest.
            path=Path(original)
            if sha(path)!=frozen['checkpoint_hashes'][original]:
                raise ValueError('selected checkpoint changed')
            target='checkpoints/'+name+path.suffix
            copy(path,target)
            definition['checkpoint']=target
        definitions[name]={'frozen_name':key,'definition':definition}
    manifest={'schema_version':1,'protocol':'live_v3_window120_r1',
              'files':files,'definitions':definitions,'frozen_hash':frozen['hash'],
              'runtime_hash':source['runtime_hash'],'training':False,'reserved_reads':False}
    manifest['hash']=digest(manifest)
    atomic_json(destination/'manifest.json',manifest)
    return manifest

class Assets:
    def __init__(self,bundle):
        self.root=Path(bundle).resolve()
        self.manifest=checked_record(self.root/'manifest.json')
        for name,expected in self.manifest['files'].items():
            path=(self.root/name).resolve()
            if not path.is_relative_to(self.root) or sha(path)!=expected:
                raise ValueError('inference file changed: '+name)
        self.frozen=checked_record(self.root/'frozen.json')
        source=checked_record(self.root/'source.json')
        if self.frozen['hash']!=self.manifest['frozen_hash'] or source['runtime_hash']!=self.frozen['runtime_hash']:
            raise ValueError('frozen provenance changed')
        # Windows snapshot keys are provenance. Compare text and canonical keys on Linux too.
        package=Path(__file__).parents[1]/'rca_investigation'
        actual={str(PureWindowsPath(p.relative_to(package))):p.read_text(encoding='utf-8') for p in package.rglob('*.py')}
        expected={str(PureWindowsPath(k)):v for k,v in source['files'].items()}
        if actual!=expected or digest({k:digest(v) for k,v in source['files'].items()})!=source['runtime_hash']:
            raise ValueError('investigation source differs from frozen v3 snapshot')
        self.topology=sockshop_topology()
        self.config=TaskConfig(canonical(json.loads((self.root/'config.json').read_text())))
        if self.config.hash!=self.frozen['config_hash']:
            raise ValueError('exported config changed')
        self.specs=build_catalogue(self.config,self.topology)
        self.encoder=EncoderV3.load(self.root/'encoder.json')
        self.model=load_model(self.root/'model.json',self.config,self.specs)
        if self.encoder.hash!=self.config.values['encoding_hash'] or self.model.hash!=self.frozen['model_hash']:
            raise ValueError('inference model/encoder changed')
        if sha(self.root/'core.lock')!=self.frozen['environment']['core_lock_sha256'] or sha(self.root/'neural.lock')!=self.frozen['environment']['neural_lock_sha256']:
            raise ValueError('dependency provenance changed')
        for name,row in self.manifest['definitions'].items():
            frozen_definition=dict(self.frozen['definitions'][row['frozen_name']])
            relocated=dict(row['definition'])
            if 'checkpoint' in frozen_definition:
                original=frozen_definition.pop('checkpoint')
                checkpoint=relocated.pop('checkpoint')
                if sha(self.root/checkpoint)!=self.frozen['checkpoint_hashes'][original]:
                    raise ValueError('relocated checkpoint differs from frozen selection')
            if frozen_definition!=relocated:
                raise ValueError('live policy definition differs from frozen selection')
        pin=json.loads((self.root/'helper-pin.json').read_text())
        if sha(self.root/'helper')!=pin['helper_sha256']:
            raise ValueError('helper checksum changed')

    def policy(self,name):
        if name not in POLICIES or name not in self.manifest['definitions']:
            raise ValueError('unsupported policy')
        from rca_investigation.policies.dp import BoundedDP
        from rca_investigation.policies.expected_sarsa import TabularPolicy,HybridPolicy
        from rca_investigation.policies.rules import RulePolicy
        from rca_investigation.policies.rules_v3 import ExpandedSetRule
        definition=self.manifest['definitions'][name]['definition']
        objective=self.config.values['objective']
        def planner(depth):
            return BoundedDP(self.model,self.specs,depth=depth,max_expanded_states=50000,
                correct=objective['correct'],incorrect=objective['incorrect'],defer=objective['defer'])
        kind=definition['kind']
        if kind in ('dp','voi'): policy=planner(definition['depth'])
        elif kind=='expanded-rule': policy=ExpandedSetRule(self.specs)
        elif kind=='rule': policy=RulePolicy(definition['name'],definition.get('seed',11),objective)
        else:
            path=self.root/definition['checkpoint']
            if kind=='dqn':
                import torch,numpy
                from rca_investigation.state_vector import StateVectorSchema
                from rca_investigation.policies.dqn import DQNPolicy
                torch.set_num_threads(1)
                if torch.__version__!=self.frozen['environment']['torch'] or numpy.__version__!=self.frozen['environment']['numpy']:
                    raise ValueError('install the pinned neural environment before DQN inference')
                checkpoint=torch.load(path,map_location='cpu',weights_only=False)
                schema=StateVectorSchema(checkpoint['feature_schema'])
                expected=StateVectorSchema.build(self.specs,self.model,budget=10,probes=5,objective=objective)
                if schema.hash!=expected.hash or checkpoint['identity']['neural_lock_sha256']!=sha(self.root/'neural.lock'):
                    raise ValueError('DQN schema/lock changed')
                policy=DQNPolicy(schema,seed=definition['seed'],hidden=checkpoint['identity']['settings']['hidden'])
                policy.online.load_state_dict(checkpoint['online'])
            else:
                checkpoint=json.loads(path.read_text())
                policy=TabularPolicy.restore(checkpoint['policy'])
            identity=checkpoint['identity']
            if identity['runtime_hash']!=self.frozen['runtime_hash'] or identity['model_hash']!=self.model.hash or identity['task_hash']!=self.config.hash or policy.hash!=checkpoint['policy_hash']:
                raise ValueError('checkpoint identity changed')
            if kind=='hybrid': policy=HybridPolicy(policy,planner(2),min_visits=5)
        return policy,digest({'bundle':self.manifest['hash'],'policy':name,'definition':definition})
