"""Fresh eleven-scenario engineering checks plus all-policy shadow comparisons.

One physical injection per scenario. DQN is the live primary; every other policy
investigates that same measured public window after recovery. This is NOT a new
reserved benchmark or physical live repair check for every shadow policy.
"""
import argparse,json,sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rca_live_v3.assets import Assets,POLICIES
from rca_live_v3.controller import Controller,investigate
from rca_live_v3.bus import server_lock
from rca_investigation.schedule_v3 import SCENARIOS
from rca_investigation.contracts_v2 import decode_record
from rca_investigation.backends.replay import ReplayBackend
from rca_investigation.dataset import atomic_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',default='runs/live_v3_export')
    p.add_argument('--registry',default='runs/live_v3/deployment.json')
    p.add_argument('--output',default='runs/live_v3_verification')
    p.add_argument('--case',choices=tuple(SCENARIOS))
    args=p.parse_args()
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    target=output/'verification.json'
    c=Controller(Path.cwd(),args.bundle,args.registry,output,collection='windowed')
    report=json.loads(target.read_text()) if target.exists() else {'checks':{},'complete':False,'training':False,'reserved_reads':False,'benchmark_equivalent':False,'bundle_hash':c.assets.manifest['hash']}
    if report['bundle_hash']!=c.assets.manifest['hash']: raise ValueError('verification bundle changed')
    with server_lock(output):
        for case in ([args.case] if args.case else SCENARIOS):
            if case in report['checks']: continue
            receipt=c.run({'id':str(uuid4()),'command':'run','case':case,'policy':'dqn'})
            folder=output/'incidents'/receipt['id']
            incident=decode_record(json.loads((folder/'public.json').read_text()),'public')
            packets=tuple(decode_record(v,'packet') for v in json.loads((folder/'responses.json').read_text()))
            outcomes={}
            for name in POLICIES:
                backend=ReplayBackend(packets,c.assets.specs)
                outcomes[name]=investigate(c.assets,name,incident,backend)
            truth=sorted(cause for cause,_ in SCENARIOS[case])
            for value in outcomes.values():
                d=value['decision']
                value['exact_set_correct']=d['decision_type']=='DIAGNOSE' and sorted(d['cause_ids'])==truth
            d=receipt['diagnosis']
            primary_correct=d['decision_type']=='DIAGNOSE' and sorted(d['cause_ids'])==truth
            report['checks'][case]={'receipt':str(folder/'receipt.json'),'health_confirmed':receipt['health']['confirmed'],
                'primary_correct':primary_correct,'primary_agent_repair':receipt['agent_repair'],'policies':outcomes}
            report['complete']=set(report['checks'])==set(SCENARIOS)
            report['integration_passed']=all(v['health_confirmed'] for v in report['checks'].values())
            atomic_json(target,report)
            print('Completed '+case+'. Live DQN correct: '+str(primary_correct),flush=True)
    return 0
if __name__=='__main__': raise SystemExit(main())
