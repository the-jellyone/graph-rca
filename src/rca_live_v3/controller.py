"""One v3 lab owner, coherent evidence, policy choice and verified recovery."""
import json
import os
import requests
from pathlib import Path
from time import monotonic,sleep,perf_counter
from uuid import uuid4
from contextlib import ExitStack
from datetime import timedelta
from rca_investigation.capture import capture_lease,RecoveryFailed
from rca_investigation.capture_v3 import collect_window,verify_components
from rca_investigation.contracts import digest
from rca_investigation.contracts_v3 import IncidentPublicV3,ObjectiveV3
from rca_investigation.catalogue import catalogue_hash
from rca_investigation.dataset import atomic_json
from rca_investigation.encoding_v3 import RawEvidenceBackendV3
from rca_investigation.environment import InvestigationEnv
from rca_investigation.faults_v3 import FaultTransaction
from rca_investigation.backends.container_stats import ContainerStatsCollector
from rca_investigation.workload_v3 import WorkloadV3,shipping_health_probe,operation_succeeded
from rca_investigation.schedule_v3 import SCENARIOS
from rca_investigation.raw_schedule import settle_states
from .assets import Assets,POLICIES
from .lab import DemoDriver
from .graph import Graph
from .bus import emit,pending,server_lock
from .legacy_graph.graph_writer import writer_lease

def validate_request(item):
    if set(item)-{'id','command','case','policy'} or item.get('command') not in ('run','stop','policy'):
        raise ValueError('unsupported command')
    if item.get('policy','dqn') not in POLICIES: raise ValueError('unsupported policy')
    if item['command']=='run' and item.get('case') not in SCENARIOS: raise ValueError('unsupported scenario')

def repair_supported(result,components):
    decision=result['decision']
    if not components or decision['decision_type']!='DIAGNOSE' or decision['confidence']<.90:
        return False
    if set(decision['cause_ids'])!=set(components): return False
    if result.get('protocol_hash'):
        from .early import now
        from rca_investigation.contracts import timestamp
        if any(not 0 <= (timestamp(now())-timestamp(p['sample_time'])).total_seconds() <=15 for p in result['packets']):
            return False
    for cause,row in components.items():
        family,codes={'container_stopped':('status',{'STOPPED'}),'cpu_contention':('cpu',{'HIGH'}),
            'memory_pressure':('memory',{'PRESSURE','PRESSURE_AND_GROWTH'}),'dependency_latency':('latency',{'SLOW'})}[row['mechanism']]
        resource='front-end' if row['mechanism']=='dependency_latency' else row['resource_id']
        if not any(p['action_id']==f'{family}/{resource}/v2' and p['availability']=='OK' and p['response_code'] in codes for p in result['packets']):
            return False
    return True

def investigate(assets,name,incident,backend,event=lambda *args:None):
    policy,identity=assets.policy(name)
    obj=assets.config.values['objective']
    env=InvestigationEnv(backend.topology if hasattr(backend,'topology') else assets.topology,
        assets.specs,assets.model,backend,defer_utility=obj['defer'],correct_utility=obj['correct'],incorrect_utility=obj['incorrect'])
    env.reset(incident)
    trace=[]
    try:
        while not env.terminated:
            began=perf_counter()
            action=policy.choose(env.observation())
            latency=(perf_counter()-began)*1000
            _,reward,done=env.step(action,policy_hash=identity)
            row={'action':action,'decision_latency_ms':latency,'credits':env.ledger.credits,'terminal':done,'reward':reward}
            if action not in ('STOP','DEFER'):
                row['response']=env.ledger.packets[-1].response_code
                event('probe',row)
            trace.append(row)
        result={'policy':name,'decision':env.decision.to_dict(),'trace':trace,'credits':env.ledger.credits,
                'probes':env.probes,'packets':[p.to_dict() for p in env.ledger.packets],
                'bundle_hash':assets.manifest['hash'],'benchmark_equivalent':False,'trained_during_demo':False}
        event('diagnosis',result)
        return result
    finally: env.close()

class Controller:
    def __init__(self,root,bundle,registry,output,collection='early'):
        if collection not in ('early','windowed'): raise ValueError('unsupported collection mode')
        self.collection=collection
        self.root=Path(root).resolve()
        self.output=Path(output).resolve()
        self.assets=Assets(bundle)
        self.driver=DemoDriver(self.root,self.assets,registry)
        self.registry=self.driver.registry
        self.shared=self.root/'runs/adaptive_v2/lab'
        self.emit=lambda kind,payload:emit(self.output,kind,payload)
        self.graph_factory=lambda:Graph(self.assets.topology)

    def health(self,workload,transaction,attempts=90):
        deadline=monotonic()+attempts
        checks=[]
        while monotonic()<deadline:
            try:
                if any(v!='running' for v in self.driver.states().values()) or self.driver.proxy_control()['delay_ms']!=0:
                    raise RuntimeError('resource/proxy not healthy')
                workload.prepare()
                consecutive=[]
                for _ in range(5):
                    rows=[workload.fresh_auth(),workload.checkout(),workload.shipping_health()]
                    checks.append(rows)
                    if not all(operation_succeeded(r) for r in rows): break
                    consecutive.append(rows)
                memory_ok=True
                for component in transaction.state['components'] if transaction else ():
                    if component['mechanism']=='memory_pressure':
                        sample=next(r for r in ContainerStatsCollector(self.registry).poll() if r['resource_id']=='carts')
                        memory_ok &= sample['availability']=='OK' and sample['memory_bytes']<=component['baseline_memory_bytes']+max(16*1024**2,self.registry['carts']['memory_limit_bytes']*.05)
                if len(consecutive)==5 and memory_ok:
                    return {'confirmed':True,'operation_groups':checks,'consecutive_groups':5,'memory_released':memory_ok}
            except (RuntimeError,ValueError,requests.RequestException) as exc:
                checks.append({'error_type':type(exc).__name__})
            sleep(2)
        raise RecoveryFailed('five consecutive auth, checkout and shipping checks did not pass')

    def run(self,item):
        validate_request(item)
        # A per-incident call remains exclusive with legacy and v3 collectors.
        from .ownership import reject_foreign_controllers
        reject_foreign_controllers()
        with capture_lease(self.shared),writer_lease(self.root/'data/graph_writer.lock'),self.graph_factory() as graph:
            legacy=self.root/'runs/integration_v1/captures/sockshop_v1'
            if any((legacy/n).exists() for n in ('capture.lock','blocked-lab.json')):
                raise RecoveryFailed('legacy lab ownership is unresolved')
            self.driver.resolve()
            return self._run(item,graph)

    def _run(self,item,graph):
        identifier=str(uuid4())
        directory=self.output/'incidents'/identifier
        directory.mkdir(parents=True)
        transaction=None
        workload=WorkloadV3(shipping_probe=lambda:shipping_health_probe(self.driver))
        early=getattr(self,'collection','windowed')=='early'
        if early:
            from .early import PROTOCOL
        receipt={'id':identifier,'request_id':item['id'],'case':item['case'],'policy':item['policy'],
            'status':'STARTED','protocol':PROTOCOL['name'] if early else 'live_v3_window120_r1','pid':os.getpid(),
            'bundle_hash':self.assets.manifest['hash'],'benchmark_equivalent':False,'training':False,
            'agent_repair':False,'resolved':False}
        source={str(p.relative_to(Path(__file__).parent)):p.read_text(encoding='utf-8') for p in sorted(Path(__file__).parent.rglob('*.py'))}
        receipt['adapter_hash']=digest(source)
        atomic_json(directory/'adapter-source.json',{'files':source,'hash':receipt['adapter_hash']})
        atomic_json(directory/'receipt.json',receipt)
        started=monotonic()
        request_started=started
        stack=ExitStack()
        collector=None
        try:
            self.emit('stage',{'message':'Checking healthy auth, checkout and shipping before injection.'})
            receipt['baseline']=self.health(workload,None)
            workload.prepare()
            if early:
                from .early import RollingCollector,PROTOCOL,PROTOCOL_HASH
                collector=stack.enter_context(RollingCollector(self.driver,workload,self.registry))
                receipt['protocol_hash']=PROTOCOL_HASH
                atomic_json(directory/'live-protocol.json',{'protocol':PROTOCOL,'hash':PROTOCOL_HASH})
            transaction=FaultTransaction(self.driver,self.shared/'blocked-lab.json',owner=identifier)
            started=monotonic()
            receipt['latency_origin']='injection_requested'
            self.emit('latency',{'elapsed_seconds':0.,'stage':'Starting fault injection'})
            components=transaction.inject(SCENARIOS[item['case']])
            receipt['injection_command_seconds']=monotonic()-started
            expected={r:'running' for r in self.driver.topology.resources}
            for row in components.values():
                if row['mechanism']=='container_stopped': expected[row['resource_id']]='exited'
            if early:
                result,checks,context=self._observe_early(identifier,item,graph,collector,directory,components,started)
                receipt['effect_checks']=checks
                receipt['graph_context']=context
                receipt['observation_seconds']=result['observation_seconds']
            else:
                self.emit('stage',{'message':'Fault active. Settling for 30 seconds, then collecting a coherent 120-second window.'})
                settle_states(self.driver,expected,30)
                window,raw=collect_window(self.driver,workload,None,self.registry,expected)
                atomic_json(directory/'observations.json',{'window':window,'measurements':raw})
                receipt['effect_checks']=verify_components(components,window,raw,self.registry,self.driver)
                objective=self.assets.config.values['objective']
                incident=IncidentPublicV3(identifier,graph.topology.hash,catalogue_hash(self.assets.specs),'front-end','manual_frontend',
                    window['window_start'],window['window_end'],10,5,objective['lambda_cost'],
                    task_id=self.assets.config.values['task_id'],encoding_hash=self.assets.encoder.hash,
                    objective_hash=digest(ObjectiveV3(**objective).to_dict()))
                backend=RawEvidenceBackendV3(self.assets.specs,raw,graph.topology,self.assets.encoder)
                backend.begin(incident)
                try: packets=tuple(backend.execute(s) for s in self.assets.specs)
                finally: backend.close()
                # Private scenario/transaction metadata never enters policy observations.
                atomic_json(directory/'public.json',incident.to_dict())
                atomic_json(directory/'responses.json',[p.to_dict() for p in packets])
                receipt['graph_context']=graph.publish(incident,packets)
                self.emit('stage',{'message':'Fresh window published to Neo4j. Policy is buying graph evidence.'})
                backend=graph.backend(self.assets.specs,incident)
                backend.topology=graph.topology
                result=investigate(self.assets,item['policy'],incident,backend,self.emit)
            # Quiesce independent traffic before repair and functional recovery checks.
            stack.close()
            atomic_json(directory/'diagnosis.json',result)
            receipt.update(diagnosis=result['decision'],diagnosis_seconds=monotonic()-started)
            self.emit('latency',{'elapsed_seconds':receipt['diagnosis_seconds'],'stage':'Diagnosis completed'})
            receipt['healthy_control_confirmed']=not components and result['decision']['decision_type']=='DIAGNOSE' and not result['decision']['cause_ids']
            if repair_supported(result,components) and all(c['passed'] for c in receipt['effect_checks'].values()):
                self.emit('stage',{'message':'Purchased evidence supports every owned cause. Applying recorded component repairs.'})
                transaction.recover()
                receipt['agent_repair']=True
                receipt['repair_command_completed_seconds']=monotonic()-started
                self.emit('latency',{'elapsed_seconds':receipt['repair_command_completed_seconds'],'stage':'Repair command completed'})
            elif components:
                self.emit('stage',{'message':'No permitted agent repair. Injector cleanup will restore its own components.'})
            else:
                self.emit('stage',{'message':'Healthy control. No repair command is needed.'})
            receipt['status']='COMPLETE'
            return receipt
        except BaseException as exc:
            receipt.update(status='FAILED',error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            try:
                stack.close()
                if collector is not None and any(t.is_alive() for t in collector.threads):
                    raise RecoveryFailed('rolling traffic still active; preserve recovery journal and lab ownership')
                if transaction is not None:
                    receipt['component_cleanup']=transaction.recover()
                receipt['health']=self.health(workload,transaction)
                if transaction is not None:
                    atomic_json(directory/'recovery.json',transaction.state)
                    transaction.confirm_recovery(lambda:True)
                graph.clear(identifier)
                receipt['cleanup_confirmed']=True
                receipt['resolved']=receipt['agent_repair'] and receipt['health']['confirmed']
                receipt['verified_recovery_seconds']=monotonic()-started
                receipt['overall_latency_seconds']=receipt['verified_recovery_seconds']
            except BaseException as exc:
                receipt.update(status='RECOVERY_FAILED',recovery_error_type=type(exc).__name__)
                if transaction is None:
                    transaction=FaultTransaction(self.driver,self.shared/'blocked-lab.json',owner=identifier)
                    transaction.save()
                raise
            finally:
                receipt['controller_total_seconds']=monotonic()-request_started
                receipt.setdefault('overall_latency_seconds',monotonic()-started)
                workload.close()
                atomic_json(directory/'receipt.json',receipt)
                self.emit('result',receipt)

    def _observe_early(self,identifier,item,graph,collector,directory,components,started):
        from .early import now,prefix_packets,ProgressiveSession
        from rca_investigation.contracts import timestamp
        start=now(); end_limit=timestamp(start)+timedelta(seconds=120)
        session=ProgressiveSession(self.assets,identifier,item['policy'],self.emit)
        last_report=-10.
        self.emit('stage',{'message':'Collecting fresh evidence. Early decisions require 90% confidence, fresh paid support and at least 15 seconds of observation.'})
        while True:
            end=min(timestamp(now()),end_limit).isoformat()
            if timestamp(end)<=timestamp(start): sleep(.05); continue
            window,raw=collector.snapshot(start,end)
            incident,packets,ready=prefix_packets(self.assets,identifier,graph.topology,window,raw)
            context=graph.publish(incident,packets)
            deadline=timestamp(end)>=end_limit
            result=session.advance(incident,graph,ready,context,packets,deadline=deadline)
            elapsed=monotonic()-started
            atomic_json(directory/'progress.json',{'elapsed_seconds':elapsed,'ready_actions':sorted(ready),
                'probes':len(session.ledger.packets),'credits':session.ledger.credits,'deadline_reached':deadline})
            if elapsed-last_report>=10:
                self.emit('latency',{'elapsed_seconds':elapsed,'stage':f'Collecting evidence; {len(ready)} tools ready, {len(session.ledger.packets)} paid probes'})
                last_report=elapsed
            if result:
                atomic_json(directory/'observations.json',{'window':window,'measurements':raw})
                atomic_json(directory/'public.json',incident.to_dict())
                atomic_json(directory/'responses.json',[p.to_dict() for p in packets])
                # Effect checks are private engineering receipts, never policy inputs.
                checks={}
                from .early import supported
                for cause,component in components.items():
                    passed=supported((cause,),packets)
                    if component['mechanism']=='dependency_latency':
                        state=self.driver.proxy_control()
                        passed &= state['delay_ms']==800 and state['forwarded_requests']-component['proxy_before']['forwarded_requests']>=20
                    checks[cause]={'passed':bool(passed),'scope':'live_prefix_effect_only','benchmark_equivalent':False}
                return result,checks,context
            sleep(.5)

    def serve(self):
        default='dqn'
        with server_lock(self.output):
            self.emit('stage',{'message':'V3 controller ready. Select one of eleven scenarios in Terminal 3.'})
            while True:
                for path,item in pending(self.output):
                    validate_request(item)
                    path.unlink()
                    if item['command']=='stop': return
                    if item['command']=='policy':
                        default=item['policy']
                        self.emit('stage',{'message':'Policy selected: '+default})
                        continue
                    item.setdefault('policy',default)
                    try: self.run(item)
                    except (RuntimeError,ValueError,OSError) as exc:
                        self.emit('error',{'message':str(exc)})
                        if (self.shared/'blocked-lab.json').exists(): return
                sleep(.5)
