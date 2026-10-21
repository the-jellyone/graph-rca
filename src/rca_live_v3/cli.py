"""Three-terminal v3 demo, artifact export and journalled recovery."""
import argparse
import json
import os
from pathlib import Path
from time import sleep
from uuid import uuid4
from rca_investigation.dataset import atomic_json
from rca_investigation.capture import capture_lease
from rca_investigation.faults_v3 import FaultTransaction
from rca_investigation.schedule_v3 import SCENARIOS
from rca_investigation.workload_v3 import WorkloadV3,shipping_health_probe
from .assets import Assets,POLICIES,export_bundle
from .lab import inventory
from .controller import Controller
from .bus import request,read_events,server_lock
from .narration import Narrator,load_environment
from .display import render

def dead(pid):
    if type(pid) is not int or pid<1: return False
    if os.name=='nt':
        import ctypes
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        return ctypes.windll.kernel32.GetLastError()==87
    try: os.kill(pid,0)
    except ProcessLookupError: return True
    except PermissionError: return False
    return False

def recover(controller):
    # Remove only positively dead PID locks. Never infer death from a timestamp.
    for path in (controller.output/'server.lock',controller.shared/'capture.lock',controller.root/'data/graph_writer.lock'):
        if path.exists():
            value=json.loads(path.read_text())
            if not dead(value.get('pid')): raise RuntimeError('lock owner may still be alive: '+str(path))
            path.unlink()
    controller.driver.resolve()
    journal=controller.shared/'blocked-lab.json'
    if not journal.exists(): return {'recovery_required':False}
    state=json.loads(journal.read_text())
    if state.get('schema_version')!=3: raise ValueError('use the recorded protocol owner to recover this fault')
    # capture_lease correctly rejects an unresolved marker; take only its lock here.
    with server_lock(controller.shared):
        lock=controller.shared/'capture.lock'
        descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        with os.fdopen(descriptor,'w') as stream: json.dump({'pid':os.getpid()},stream)
        workload=WorkloadV3(shipping_probe=lambda:shipping_health_probe(controller.driver))
        try:
            transaction=FaultTransaction.restore(controller.driver,journal)
            transaction.recover()
            health=controller.health(workload,transaction)
            report={'owner':state['owner'],'health':health,'components':transaction.state}
            atomic_json(controller.output/'recovery'/f"{state['owner']}.json",report)
            transaction.confirm_recovery(lambda:True)
            return report
        finally:
            workload.close()
            lock.unlink()

def chat(output,env_file,local):
    narrator=Narrator(env_file,enabled=not local)
    print('V3 narration. Ctrl+C closes this viewer. Use the injector to select a case or policy.',flush=True)
    offset=0
    try:
        while True:
            rows,offset=read_events(output,offset)
            for row in rows:
                if row['kind']=='result':
                    receipt=dict(row['payload'])
                    d=receipt.get('diagnosis')
                    if d:
                        receipt['diagnosis']={'prediction':'DEFER' if d['decision_type']=='DEFER' else ', '.join(d['cause_ids']) or 'NO_FAULT',
                                              'confidence':d['confidence']}
                    result=narrator.describe(receipt)
                    print(result['authoritative'],flush=True)
                    if result.get('commentary'): print(result['commentary'],flush=True)
                elif row['kind']=='latency':
                    p=row['payload']
                    print(f"The response has taken {p['elapsed_seconds']:.1f} seconds since the injection request. {p['stage']}.",flush=True)
                else: print(render(row['kind'],row['payload']),flush=True)
            sleep(.5)
    except KeyboardInterrupt: return

def inject(output):
    policy='dqn'
    print('Policies: '+', '.join(POLICIES))
    print('Scenarios: '+', '.join(SCENARIOS))
    print('Enter a scenario, policy <name>, or stop. One queued incident at a time.')
    while True:
        try: value=input(f'[{policy}] > ').strip()
        except (EOFError,KeyboardInterrupt): return
        if value=='stop': request(output,'stop'); return
        if value.startswith('policy '):
            name=value.split(maxsplit=1)[1]
            if name not in POLICIES: print('Unsupported policy.'); continue
            policy=name
            print('Policy selected: '+policy)
        elif value in SCENARIOS:
            identifier=request(output,'run',case=value,policy=policy)
            print('Queued '+identifier+'. Watch Terminal 1 for recovery before the next case.')
        else: print('Choose a listed scenario or policy.')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('export','inventory','check','serve','run','chat','inject','stop','recover'))
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--bundle',type=Path,default=Path('runs/live_v3_export'))
    parser.add_argument('--registry',type=Path,default=Path('runs/live_v3/deployment.json'))
    parser.add_argument('--output',type=Path,default=Path('runs/live_v3'))
    parser.add_argument('--case',choices=tuple(SCENARIOS),default='healthy')
    parser.add_argument('--policy',choices=POLICIES,default='dqn')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--local-narration',action='store_true')
    parser.add_argument('--collection',choices=('early','windowed'),default='early',
        help='early: progressive evidence, up to 120 seconds; windowed: original 30+120-second protocol')
    args=parser.parse_args(argv)
    os.chdir(args.root)
    if args.env_file: load_environment(args.env_file)
    if args.command=='export':
        result=export_bundle(args.root,args.bundle)
        print('Inference bundle exported: '+result['hash']); return 0
    if args.command=='chat': chat(args.output,args.env_file,args.local_narration); return 0
    if args.command=='inject': inject(args.output); return 0
    if args.command=='stop': request(args.output,'stop'); return 0
    assets=Assets(args.bundle)
    if args.command=='inventory':
        print(json.dumps(inventory(args.root,assets,args.registry),indent=2)); return 0
    if args.command=='check':
        loaded={name:assets.policy(name)[1] for name in POLICIES}
        print(json.dumps({'passed':True,'policies':loaded,'scenarios':list(SCENARIOS),'bundle_hash':assets.manifest['hash']})); return 0
    controller=Controller(args.root,args.bundle,args.registry,args.output,collection=args.collection)
    if args.command=='recover': print(json.dumps(recover(controller))); return 0
    if args.command=='serve': controller.serve(); return 0
    with server_lock(args.output):
        controller.run({'id':str(uuid4()),'command':'run','case':args.case,'policy':args.policy})
    return 0
