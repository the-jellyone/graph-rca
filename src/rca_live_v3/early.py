"""V2 progressive evidence acquisition adapted to V3, outside frozen training code."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from threading import Event, Lock, Thread
from time import monotonic, perf_counter
import copy
import json
import math
import requests
from rca_investigation.contracts import IncidentPublic, canonical, digest, timestamp
from rca_investigation.contracts_v2 import sha256, object_json
from rca_investigation.contracts_v3 import IncidentPublicV3, ObjectiveV3, DecisionV3
from rca_investigation.diagnosis_sets import PROFILES
from rca_investigation.catalogue import catalogue_hash
from rca_investigation.encoding_v3 import RawEvidenceBackendV3
from rca_investigation.encoding import encode_packet
from rca_investigation.operations_v3 import OperationToolV3
from rca_investigation.backends.container_stats_v3 import StreamingStatsCollector
from rca_investigation.metrics import cpu, memory
from .short_packets import ShortPacket

PROTOCOL = {'name':'live_v3_rolling_early_r2','maximum_observation_seconds':120,
    'minimum_decision_seconds':15,'cpu_minimum_seconds':10,'memory_pressure_minimum_seconds':15,
    'memory_normal_minimum_span_seconds':90,'request_minimum_seconds':15,'minimum_requests':20,
    'minimum_confidence':.90,'status_confirmations':2,'maximum_status_age_seconds':6,
    'maximum_packet_age_seconds':15,'poll_seconds':2,'buffer_seconds':180,
    'maximum_credits':10,'maximum_probes':5,'benchmark_equivalent':False,
    'refresh_is_paid':True,'exclude_cross_boundary_requests':True,
    'status_probes':'confirmed_crash_alerts_only','frozen_policy_ready_action_mask':True,
    'graph_guidance':'confirm_unbought_visible_anomalies_before_accepting_terminal_proposals'}
PROTOCOL_HASH = digest(PROTOCOL)

def now():
    return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class ShortIncidentV3(IncidentPublicV3):
    """Real elapsed time only. The benchmark's exact 120-second type stays intact."""
    def __post_init__(self):
        IncidentPublic.__post_init__(self)
        if type(self.schema_version) is not int or self.schema_version != 3 or self.task_id not in PROFILES:
            raise ValueError('unsupported short V3 incident')
        sha256(self.encoding_hash); sha256(self.objective_hash)
        if self.entry_resource != 'front-end' or object_json(self.alert_context_json) != {}:
            raise ValueError('constant frontend entry required')
        if not 0 < (timestamp(self.window_end)-timestamp(self.window_start)).total_seconds() <= 120:
            raise ValueError('short live window must have elapsed and be at most 120 seconds')

def incident_for(assets, identifier, topology, window):
    objective = assets.config.values['objective']
    return ShortIncidentV3(identifier,topology.hash,catalogue_hash(assets.specs),'front-end','manual_frontend',
        window['window_start'],window['window_end'],10,5,objective['lambda_cost'],
        task_id=assets.config.values['task_id'],encoding_hash=assets.encoder.hash,
        objective_hash=digest(ObjectiveV3(**objective).to_dict()))

class RollingCollector:
    """One bounded stream with independent auth, checkout and shipping traffic."""
    def __init__(self,driver,workload,registry):
        self.driver,self.workload,self.registry = driver,workload,registry
        self.guard,self.stop = Lock(),Event()
        self.states,self.samples,self.requests,self.errors = (deque(maxlen=n) for n in (120,2400,600,300))
        self.threads=[]
        self.failure=None
        self.stats=StreamingStatsCollector(registry)
        self.previous_stats={}
        self.previous_records={}

    def __enter__(self):
        self.stats.__enter__()
        self.threads=[Thread(target=self._poll,name='v3-rolling-status')]
        self.threads += [Thread(target=self._traffic,args=(op,call),name='v3-rolling-'+op)
            for op,call in (('auth',self.workload.fresh_auth),('checkout',self.workload.checkout),
                            ('shipping_health',self.workload.shipping_health))]
        try:
            for thread in self.threads: thread.start()
        except BaseException:
            self.__exit__(None,None,None)
            raise
        return self

    def __exit__(self,*args):
        self.stop.set()
        for thread in self.threads:
            if thread.ident is not None: thread.join(timeout=40)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError('rolling traffic did not stop; lab ownership must remain blocked')
        self.stats.__exit__(*args)

    def _poll(self):
        try:
            while not self.stop.is_set():
                began=monotonic(); observed=now()
                states=self.driver.states()
                row={'sample_time':now(),'observation_started_at':observed,'states':states}
                samples=self.interval_samples(self.stats.poll())
                with self.guard:
                    self.states.append(row)
                    self.samples.extend(samples)
                    self._prune()
                self.stop.wait(max(.05,2-(monotonic()-began)))
        except Exception as exc:
            self.failure=exc; self.stop.set()

    def interval_samples(self,rows):
        samples=[]
        for sample in rows:
            resource=sample['resource_id']
            previous=self.previous_stats.get(resource)
            if previous is not None and timestamp(previous)==timestamp(sample['sample_time']):
                if canonical(sample)!=canonical(self.previous_records[resource]):
                    raise ValueError('conflicting streamed measurements share a timestamp')
                # A Docker redraw is not another independent measurement.
                continue
            if previous is not None and timestamp(previous)>timestamp(sample['sample_time']):
                raise ValueError('Docker measurement time moved backwards')
            self.previous_stats[resource]=sample['sample_time']
            self.previous_records[resource]=sample
            # CPU rates span consecutive reports. Drop the unknown first interval.
            if previous is not None: samples.append({**sample,'observation_started_at':previous})
        return samples

    def _traffic(self,operation,call):
        try:
            while not self.stop.is_set():
                began=monotonic(); started=now()
                try:
                    row={**call(),'request_started_at':started}
                    with self.guard:
                        self.requests.append(row); self._prune()
                except (RuntimeError,requests.RequestException) as exc:
                    with self.guard:
                        self.errors.append({'sample_time':now(),'request_started_at':started,
                            'operation':operation,'reason':type(exc).__name__})
                        self._prune()
                self.stop.wait(max(.05,2-(monotonic()-began)))
        except Exception as exc:
            self.failure=exc; self.stop.set()

    def _prune(self):
        boundary=timestamp(now())-timedelta(seconds=180)
        for rows in (self.states,self.samples,self.requests,self.errors):
            while rows and timestamp(rows[0]['sample_time'])<boundary: rows.popleft()

    def snapshot(self,start,end):
        if self.failure: raise RuntimeError('rolling collection failed') from self.failure
        first,last=timestamp(start),timestamp(end)
        if not first < last: raise ValueError('window has not elapsed')
        def select(rows):
            return [copy.deepcopy(r) for r in rows if first <= timestamp(r['sample_time']) <= last
                and timestamp(r.get('request_started_at',r.get('observation_started_at',r['sample_time']))) >= first]
        with self.guard:
            states=select(self.states)
            raw={'stats':select(self.samples),'requests':select(self.requests),'status_history':states,
                'workload_errors':select(self.errors),'logs':{},'collected_at':end,'registry':self.registry,'status':{}}
        if states:
            row=states[-1]
            raw['status']={r:{'resource':r,'container_id':self.driver.ids[r],'state':value,
                'sample_time':row['sample_time']} for r,value in row['states'].items()}
        return {'window_start':start,'window_end':end},raw

class PrefixBackend(RawEvidenceBackendV3):
    def execute(self,spec):
        duration=(timestamp(self.incident.window_end)-timestamp(self.incident.window_start)).total_seconds()
        reason=None
        if spec.template=='status':
            rows=self.raw['status_history']; row=self.raw['status'].get(spec.resource_id)
            if not row or len(rows)<2 or any(r['states'].get(spec.resource_id)!=row['state'] for r in rows[-2:]) or \
                    (timestamp(self.incident.window_end)-timestamp(row['sample_time'])).total_seconds()>6:
                reason='waiting_for_fresh_status_confirmation'
        elif spec.template=='cpu' and duration<10: reason='cpu_prefix_too_short'
        elif spec.template=='memory' and duration<15: reason='memory_prefix_too_short'
        elif spec.template in ('latency','request_errors') and duration<15: reason='request_prefix_too_short'
        elif spec.template=='logs' and spec.resource_id not in self.raw['logs']: reason='logs_not_ingested'
        if reason: return self.unavailable(spec,reason)
        if spec.template not in ('cpu','memory'): return super().execute(spec)
        rows=[r for r in self.raw['stats'] if r['resource_id']==spec.resource_id]
        item=self.raw['registry'][spec.resource_id]; thresholds=self.encoder.values['thresholds']
        custom=self.encoder.values.get('thresholds_by_resource',{})
        threshold=lambda key:custom.get(key,{}).get(spec.resource_id,thresholds[key])
        if spec.template=='cpu':
            summary=cpu.summarize(rows,spec.resource_id,item['container_id'],item['cpu_quota_cores'],
                self.incident.window_start,self.incident.window_end,threshold('cpu'))
        else:
            summary=memory.summarize(rows,spec.resource_id,item['container_id'],item['memory_limit_bytes'],
                self.incident.window_start,self.incident.window_end,pressure=threshold('memory'),growth=threshold('memory_growth'))
        availability,code,evidence,why=summary
        if availability=='OK' and evidence['sample_count']<max(2,math.ceil(duration*.8/2)):
            return self.unavailable(spec,'insufficient_distinct_prefix_samples')
        if spec.template=='memory' and availability=='OK' and code not in ('PRESSURE','PRESSURE_AND_GROWTH') and evidence['growth_span_seconds']<90:
            return self.unavailable(spec,'normal_memory_requires_growth_observation')
        packet=ShortPacket(spec.action_id,spec.resource_id,availability,code,self.incident.window_start,
            self.incident.window_end,self.incident.window_end,'live_v3_prefix_r1',0,
            collected_at=self.raw['collected_at'],encoding_hash=self.encoder.hash,evidence_json=canonical(evidence),reason=why)
        packet.validate_context(spec,self.incident)
        return packet

    def unavailable(self,spec,reason):
        if isinstance(spec,OperationToolV3):
            from rca_investigation.operations_v3 import OperationResultV3
            return OperationResultV3(spec.action_id,spec.resource_id,'NO_DATA','UNAVAILABLE',
                self.incident.window_start,self.incident.window_end,self.incident.window_end,'live_v3_prefix_r1',0,
                collected_at=self.raw['collected_at'],encoding_hash=self.encoder.hash,reason=reason)
        return encode_packet(spec,self.incident,('NO_DATA','UNAVAILABLE',{},reason),'live_v3_prefix_r1',collected_at=self.raw['collected_at'])

def prefix_packets(assets,identifier,topology,window,raw):
    incident=incident_for(assets,identifier,topology,window)
    backend=PrefixBackend(assets.specs,raw,topology,assets.encoder)
    backend.begin(incident)
    try: packets=tuple(backend.execute(s) for s in assets.specs)
    finally: backend.close()
    return incident,packets,{p.action_id for p in packets if p.availability=='OK'}

class VersionedLedger:
    """One budget for the entire incident. Refresh replaces a symbol, not its cost."""
    def __init__(self): self.packets=[]; self.latest={}; self.credits=0
    def acquire(self,spec,packet,incident):
        packet.validate_context(spec,incident)
        if packet.availability!='OK' or self.credits+spec.credits>10 or len(self.packets)>=5:
            raise ValueError('unready evidence or exhausted live budget')
        if any(p.evidence_id==packet.evidence_id for p in self.packets):
            raise ValueError('identical evidence cannot be charged twice')
        self.packets.append(packet); self.latest[spec.action_id]=packet; self.credits+=spec.credits

def supported(causes,packets):
    tests={'resource/payment':(('cpu/payment/v2',{'HIGH'}),('status/payment/v2',{'STOPPED'})),
        'resource/carts':(('memory/carts/v2',{'PRESSURE','PRESSURE_AND_GROWTH'}),),
        'edge/orders/payment':(('latency/front-end/v2',{'SLOW'}),)}
    return bool(causes) and all(any(p.availability=='OK' and p.action_id==a and p.response_code in codes
        for a,codes in tests.get(cause,((f"status/{cause.split('/')[1]}/v2",{'STOPPED'}),))
        for p in packets) for cause in causes)

def direct_roots(packets):
    return {f'resource/{p.resource_id}' for p in packets if p.availability=='OK' and
        (p.response_code=='STOPPED' or p.action_id=='cpu/payment/v2' and p.response_code=='HIGH' or
         p.action_id=='memory/carts/v2' and p.response_code in ('PRESSURE','PRESSURE_AND_GROWTH'))}

class GraphGuidedPolicy:
    """V2's visible-anomaly confirmation rule, with V3 operation action IDs."""
    def __init__(self,policy,packets):
        self.policy,self.packets=policy,packets
    def choose(self,observation):
        from .graph import anomaly
        seen={a for a,_ in observation['history']}
        pending=[p.action_id for p in self.packets if anomaly(p) and p.action_id not in seen
            and p.action_id in observation['mask']]
        for prefix in (('status/',),('cpu/','memory/')):
            preferred=[a for a in pending if a.startswith(prefix)]
            if preferred: pending=preferred; break
        narrowed={**observation,'mask':tuple(pending)} if pending else observation
        action=self.policy.choose(narrowed)
        if action not in narrowed['mask']:
            if action not in ('STOP','DEFER') or not pending:
                raise ValueError('policy selected an unknown graph-masked action')
            # DP evaluates terminal utilities internally even when its caller
            # requires a visible anomaly to be confirmed by a paid probe first.
            values=getattr(self.policy,'stats',{}).get('values',{})
            action=max(pending,key=lambda a:values.get(a,float('-inf')))
        return action

class ProgressiveSession:
    def __init__(self,assets,identifier,name,event=lambda *args:None):
        self.assets,self.identifier,self.name,self.event=assets,identifier,name,event
        self.policy,self.identity=assets.policy(name)
        self.identity=digest({'policy':self.identity,'live_protocol':PROTOCOL_HASH})
        self.ledger=VersionedLedger(); self.history=(); self.trace=[]; self.finished=False

    def advance(self,incident,graph,ready,context,public_packets,deadline=False):
        if self.finished: raise RuntimeError('session already finished')
        duration=(timestamp(incident.window_end)-timestamp(incident.window_start)).total_seconds()
        if duration<15: return None
        # Avoid spending a healthy run's budget before memory growth can be ruled out.
        if not context['anomalies'] and not self.history and duration<90 and not deadline: return None
        backend=graph.backend(self.assets.specs,incident); backend.begin(incident)
        try:
            while True:
                probabilities=self.assets.model.diagnosis(self.history)
                predicted=max(self.assets.model.classes,key=lambda c:probabilities[c])
                causes=self.assets.model.decode(predicted); confidence=probabilities[predicted]
                packets=tuple(self.ledger.latest.values())
                stale=[p for p in packets if (timestamp(now())-timestamp(p.sample_time)).total_seconds()>15]
                sufficient=supported(causes,packets) and direct_roots(public_packets)<=set(causes)
                if not causes:
                    sufficient=duration>=90 and not context['anomalies'] and bool(packets)
                if confidence>=.90 and sufficient and not stale:
                    return self.finish(causes,confidence,'early_confident',incident)
                remaining=[s.action_id for s in self.assets.specs if s.action_id in ready and s.action_id not in self.ledger.latest
                    and self.ledger.credits+s.credits<=10 and len(self.ledger.packets)<5]
                crashes={p.resource_id for p in public_packets if p.availability=='OK' and p.response_code=='STOPPED'}
                remaining=[a for a in remaining if not a.startswith('status/') or a.split('/')[1] in crashes]
                refresh=next((p.action_id for p in stale if p.action_id in ready and self.ledger.credits+
                    next(s.credits for s in self.assets.specs if s.action_id==p.action_id)<=10 and len(self.ledger.packets)<5),None)
                refreshing=bool(confidence>=.90 and sufficient and stale and refresh)
                if not remaining and not refreshing:
                    if deadline or len(self.ledger.packets)>=5 or self.ledger.credits>=10:
                        return self.finish((),0.,'deadline_or_budget',incident,defer=True)
                    return None
                objective=self.assets.config.values['objective']
                observation={'topology':graph.topology.to_dict(),'topology_hash':graph.topology.hash,
                    'entry_resource':'front-end','entry_protocol':'manual_frontend','catalogue_hash':incident.catalogue_hash,
                    'history':self.history,'diagnoses':probabilities,'prices':{s.action_id:s.credits for s in self.assets.specs},
                    'remaining_credits':10-self.ledger.credits,'remaining_probes':5-len(self.ledger.packets),
                    'lambda_cost':objective['lambda_cost'],'mask':tuple(remaining)+('STOP','DEFER'),
                    'objective':{key:objective[key] for key in ('defer','correct','incorrect')},
                    'schema_version':3,'task_id':incident.task_id,'encoding_hash':incident.encoding_hash,
                    'objective_hash':incident.objective_hash,'alert_context':'{}','model_hash':self.assets.model.hash}
                began=perf_counter()
                action=refresh if refreshing else GraphGuidedPolicy(self.policy,public_packets).choose(observation)
                latency=(perf_counter()-began)*1000
                if action not in observation['mask'] and not refreshing: raise ValueError('policy selected a masked action')
                if action in ('STOP','DEFER'):
                    if deadline: return self.finish((),0.,'insufficient_support',incident,defer=True)
                    return None
                spec=next(s for s in self.assets.specs if s.action_id==action)
                packet=backend.execute(spec)
                self.ledger.acquire(spec,packet,incident)
                self.history=tuple((a,s) for a,s in self.history if a!=action)+((action,packet.symbol),)
                row={'action':action,'response':packet.response_code,'credits':self.ledger.credits,
                    'decision_latency_ms':latency,'refresh':refreshing,'elapsed_incident_seconds':duration}
                self.trace.append(row); self.event('probe',row)
        finally: backend.close()

    def finish(self,causes,confidence,reason,incident,defer=False):
        self.finished=True
        decision=DecisionV3(3,'DEFER' if defer else 'DIAGNOSE',tuple(causes),confidence,
            tuple(p.evidence_id for p in self.ledger.latest.values()),self.identity,self.assets.model.hash,reason)
        result={'policy':self.name,'decision':decision.to_dict(),'trace':self.trace,'credits':self.ledger.credits,
            'probes':len(self.ledger.packets),'packets':[p.to_dict() for p in self.ledger.latest.values()],
            'acquisition_packets':[p.to_dict() for p in self.ledger.packets],'bundle_hash':self.assets.manifest['hash'],
            'benchmark_equivalent':False,'trained_during_demo':False,'protocol_hash':PROTOCOL_HASH,'graph_guided':True,
            'observation_seconds':(timestamp(incident.window_end)-timestamp(incident.window_start)).total_seconds()}
        self.event('diagnosis',result)
        return result
