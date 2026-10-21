"""Short-window safety and accounting, independent of benchmark incident files."""
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import json
import pytest
from rca_investigation.contracts import canonical,digest
from rca_investigation.contracts_v2 import ToolSpecV2,VOCABULARIES
from rca_investigation.contracts_v3 import IncidentPublicV3
from rca_investigation.topology import sockshop_topology
from rca_live_v3.early import (PrefixBackend,ShortIncidentV3,RollingCollector,VersionedLedger,
    ProgressiveSession,supported,direct_roots,incident_for,PROTOCOL_HASH)
from rca_live_v3.short_packets import ShortPacket
from rca_live_v3.display import render
from rca_live_v3.narration import summary

BASE=datetime(2026,10,20,tzinfo=timezone.utc)
def stamp(seconds): return (BASE+timedelta(seconds=seconds)).isoformat()

def setup_backend(family,duration=20):
    encoder=SimpleNamespace(hash='a'*64,values={'thresholds':{'cpu':.5,'memory':.7,'memory_growth':1048576}})
    spec=ToolSpecV2(f'{family}/payment/v2','payment',family,2,canonical({} if family=='status' else {'window_seconds':120}),
        1,(family,),VOCABULARIES[family],encoder.hash)
    assets=SimpleNamespace(config=SimpleNamespace(values={'objective':{'lambda_cost':.05,'correct':1.,'incorrect':0.,'defer':.25},
        'task_id':'sockshop_multicause_v3'}),encoder=encoder,specs=(spec,))
    topology=sockshop_topology()
    incident=incident_for(assets,'synthetic',topology,{'window_start':stamp(0),'window_end':stamp(duration)})
    registry={'payment':{'container_id':'id','cpu_quota_cores':1.,'memory_limit_bytes':100000000}}
    stats=[{'resource_id':'payment','container_id':'id','sample_time':stamp(n),'availability':'OK',
        'units':'docker_cpu_percent','cpu_percent':95.,'quota_cores':1.,'memory_bytes':80000000,
        'memory_limit_bytes':100000000,'memory_source':'docker_cli_stats_linux_v2'} for n in range(0,int(duration)+1,2)]
    raw={'registry':registry,'stats':stats,'requests':[],'logs':{},'collected_at':stamp(duration),
        'status_history':[{'sample_time':stamp(n),'states':{'payment':'exited'}} for n in (duration-2,duration)],
        'status':{'payment':{'container_id':'id','sample_time':stamp(duration),'state':'exited'}}}
    backend=PrefixBackend((spec,),raw,topology,encoder); backend.begin(incident)
    return assets,spec,incident,raw,backend

def test_short_incident_does_not_weaken_frozen_contract():
    _,_,incident,_,_=setup_backend('cpu')
    assert ShortIncidentV3.from_dict(incident.to_dict())==incident
    with pytest.raises(ValueError,match='120 second'): IncidentPublicV3.from_dict(incident.to_dict())
    with pytest.raises(ValueError): ShortIncidentV3.from_dict({**incident.to_dict(),'window_end':stamp(121)})

def test_cpu_needs_real_coverage_and_never_fabricates_48_samples():
    _,spec,incident,raw,backend=setup_backend('cpu')
    packet=backend.execute(spec)
    assert packet.response_code=='HIGH'
    assert json.loads(packet.evidence_json)['sample_count']==11
    assert packet.window_end==stamp(20)
    packet.validate_context(spec,incident)
    backend.raw={**raw,'stats':raw['stats'][:2]}
    assert backend.execute(spec).availability=='NO_DATA'

def test_cpu_and_memory_wait_for_minimum_duration_and_normal_memory_span():
    _,spec,_,_,backend=setup_backend('cpu',8)
    assert backend.execute(spec).availability=='NO_DATA'
    _,spec,_,raw,backend=setup_backend('memory')
    assert backend.execute(spec).response_code=='PRESSURE'
    backend.raw={**raw,'stats':[{**r,'memory_bytes':10000000} for r in raw['stats']]}
    assert backend.execute(spec).availability=='NO_DATA'

def test_status_requires_two_consistent_recent_observations():
    _,spec,_,raw,backend=setup_backend('status')
    assert backend.execute(spec).response_code=='STOPPED'
    backend.raw={**raw,'status_history':raw['status_history'][:1]}
    assert backend.execute(spec).availability=='NO_DATA'
    backend.raw={**raw,'status':{'payment':{**raw['status']['payment'],'sample_time':stamp(10)}}}
    assert backend.execute(spec).availability=='NO_DATA'

def test_cross_boundary_requests_and_status_are_excluded():
    c=object.__new__(RollingCollector)
    from threading import Lock
    c.guard=Lock(); c.failure=None; c.registry={}; c.driver=SimpleNamespace(ids={'payment':'id'})
    c.states=[{'sample_time':stamp(2),'observation_started_at':stamp(-1),'states':{'payment':'running'}},
        {'sample_time':stamp(4),'observation_started_at':stamp(3),'states':{'payment':'exited'}}]
    c.samples=[]; c.errors=[]
    c.requests=[{'sample_time':stamp(3),'request_started_at':stamp(-1)},
                {'sample_time':stamp(5),'request_started_at':stamp(1)}]
    _,raw=c.snapshot(stamp(0),stamp(10))
    assert len(raw['requests'])==1 and len(raw['status_history'])==1
    assert raw['status']['payment']['state']=='exited'

def test_docker_redraws_are_not_counted_or_given_conflicting_interval_metadata():
    c=object.__new__(RollingCollector); c.previous_stats={}; c.previous_records={}
    first={'resource_id':'payment','sample_time':stamp(0),'cpu_percent':10.}
    second={**first,'sample_time':stamp(2)}
    rows=c.interval_samples([first,first,second,second])
    assert len(rows)==1 and rows[0]['observation_started_at']==stamp(0)
    assert c.interval_samples([second])==[]
    with pytest.raises(ValueError,match='conflicting'):
        c.interval_samples([{**second,'cpu_percent':99.}])

def test_refresh_costs_accumulate_and_budget_does_not_reset():
    _,spec,incident,_,backend=setup_backend('cpu')
    packet=backend.execute(spec); ledger=VersionedLedger()
    ledger.acquire(spec,packet,incident)
    with pytest.raises(ValueError,match='identical'): ledger.acquire(spec,packet,incident)
    for n in range(1,5):
        updated=ShortPacket.from_dict({**packet.to_dict(),'source':'refresh'+str(n)})
        ledger.acquire(spec,updated,incident)
    assert ledger.credits==5 and len(ledger.latest)==1
    with pytest.raises(ValueError,match='exhausted'): ledger.acquire(spec,packet,incident)

def test_pair_needs_both_supported_causes_and_payment_stop_is_supported():
    p=lambda action,code:SimpleNamespace(action_id=action,resource_id=action.split('/')[1],availability='OK',response_code=code)
    packets=[p('status/catalogue-db/v2','STOPPED'),p('cpu/payment/v2','HIGH')]
    assert supported(('resource/catalogue-db','resource/payment'),packets)
    assert not supported(('resource/catalogue-db','resource/payment'),packets[:1])
    assert direct_roots(packets)=={'resource/catalogue-db','resource/payment'}
    assert supported(('resource/payment',),[p('status/payment/v2','STOPPED')])

def test_overall_latency_visible_in_both_terminal_formats_even_on_failure():
    receipt={'status':'FAILED','overall_latency_seconds':24.3}
    assert '24.3 s' in render('result',receipt)
    assert '24.3 seconds' in summary(receipt)
    assert 'final recorded outcome' in summary(receipt)

def test_graph_preserves_short_packet_validation_and_checksum():
    from rca_live_v3.graph import PaidGraphBackend
    _,spec,incident,_,backend=setup_backend('cpu')
    packet=backend.execute(spec)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def run(self,*args,**kwargs):return [{'payload':json.dumps(packet.to_dict()),'hash':digest(packet.to_dict())}]
    graph_backend=PaidGraphBackend(SimpleNamespace(session=lambda:Session()),(spec,),incident)
    graph_backend.begin(incident)
    restored=graph_backend.execute(spec)
    assert isinstance(restored,ShortPacket) and restored==packet

def test_progressive_session_never_uses_private_labels_and_waits_on_early_stop(monkeypatch):
    assets,_,incident,_,_=setup_backend('status')
    model=SimpleNamespace(classes=('healthy',),hash='b'*64,diagnosis=lambda history:{'healthy':1.},decode=lambda key:())
    assets.model=model
    assets.policy=lambda name:(SimpleNamespace(choose=lambda observation:'STOP'),'c'*64)
    assets.manifest={'hash':'bundle'}
    backend=SimpleNamespace(begin=lambda incident:None,close=lambda:None)
    graph=SimpleNamespace(topology=sockshop_topology(),backend=lambda *args:backend)
    session=ProgressiveSession(assets,'x','dqn')
    assert session.advance(incident,graph,set(),{'anomalies':[]},[]) is None
    result=session.advance(incident,graph,set(),{'anomalies':[]},[],deadline=True)
    assert result['decision']['decision_type']=='DEFER' and result['probes']==0
    assert result['protocol_hash']==PROTOCOL_HASH

def test_progressive_observation_matches_dqn_feature_contract():
    from rca_investigation.state_vector import StateVectorSchema
    assets,spec,incident,_,projector=setup_backend('status')
    assets.model=SimpleNamespace(classes=('healthy',),hash='b'*64,diagnosis=lambda history:{'healthy':1.},decode=lambda key:())
    schema=StateVectorSchema.build(assets.specs,assets.model,objective=assets.config.values['objective'])
    calls=[]
    def choose(observation):
        calls.append(schema.encode(observation)); return 'STOP'
    assets.policy=lambda name:(SimpleNamespace(choose=choose),'c'*64)
    assets.manifest={'hash':'bundle'}
    backend=SimpleNamespace(begin=lambda incident:None,close=lambda:None,execute=lambda spec:projector.execute(spec))
    graph=SimpleNamespace(topology=sockshop_topology(),backend=lambda *args:backend)
    session=ProgressiveSession(assets,'x','dqn')
    public=[SimpleNamespace(action_id=spec.action_id,resource_id='payment',availability='OK',response_code='STOPPED')]
    assert session.advance(incident,graph,{spec.action_id},{'anomalies':[{'name':'payment'}]},public) is None
    assert len(calls)==1

@pytest.mark.parametrize('confidence,second_root',[(.89,False),(.99,True)])
def test_low_confidence_or_missing_second_root_waits_then_defers(monkeypatch,confidence,second_root):
    import rca_live_v3.early as early
    assets,spec,incident,_,backend=setup_backend('status')
    packet=backend.execute(spec)
    assets.model=SimpleNamespace(classes=('fault','healthy'),hash='b'*64,
        diagnosis=lambda history:{'fault':confidence,'healthy':1-confidence},
        decode=lambda key:('resource/payment',) if key=='fault' else ())
    choose=lambda observation:spec.action_id if spec.action_id in observation['mask'] else 'STOP'
    assets.policy=lambda name:(SimpleNamespace(choose=choose),'c'*64)
    assets.manifest={'hash':'bundle'}
    backend=SimpleNamespace(begin=lambda incident:None,close=lambda:None,execute=lambda spec:packet)
    graph=SimpleNamespace(topology=sockshop_topology(),backend=lambda *args:backend)
    public=[packet]
    if second_root: public.append(SimpleNamespace(action_id='status/catalogue-db/v2',resource_id='catalogue-db',availability='OK',response_code='STOPPED'))
    monkeypatch.setattr(early,'now',lambda:stamp(20))
    session=ProgressiveSession(assets,'x','dqn')
    assert session.advance(incident,graph,{spec.action_id},{'anomalies':[{'name':'payment'}]},public) is None
    assert session.ledger.credits==1
    final=session.advance(incident,graph,{spec.action_id},{'anomalies':[{'name':'payment'}]},public,deadline=True)
    assert final['decision']['decision_type']=='DEFER' and final['credits']==1

def test_stale_evidence_cannot_authorize_early_repair(monkeypatch):
    from rca_live_v3.controller import repair_supported
    import rca_live_v3.early as early
    _,spec,_,_,backend=setup_backend('status')
    packet=backend.execute(spec)
    result={'decision':{'decision_type':'DIAGNOSE','cause_ids':['resource/payment'],'confidence':.99},
        'packets':[packet.to_dict()],'protocol_hash':PROTOCOL_HASH}
    components={'resource/payment':{'mechanism':'container_stopped','resource_id':'payment'}}
    monkeypatch.setattr(early,'now',lambda:stamp(36))
    assert not repair_supported(result,components)
    monkeypatch.setattr(early,'now',lambda:stamp(25))
    assert repair_supported(result,components)

def test_graph_guidance_confirms_visible_roots_before_downstream_symptoms():
    from rca_live_v3.early import GraphGuidedPolicy
    public=[SimpleNamespace(action_id='latency/front-end/v2',availability='OK',response_code='SLOW'),
        SimpleNamespace(action_id='cpu/payment/v2',availability='OK',response_code='HIGH'),
        SimpleNamespace(action_id='status/catalogue-db/v2',availability='OK',response_code='STOPPED')]
    observed=[]
    def choose(observation): observed.append(observation['mask']); return 'STOP'
    policy=SimpleNamespace(choose=choose,stats={'values':{}})
    wrapper=GraphGuidedPolicy(policy,public)
    observation={'history':(),'mask':tuple(p.action_id for p in public)+('STOP','DEFER')}
    assert wrapper.choose(observation)=='status/catalogue-db/v2'
    observation['history']=(('status/catalogue-db/v2','OK:STOPPED'),)
    assert wrapper.choose(observation)=='cpu/payment/v2'
    assert observed==[('status/catalogue-db/v2',),('cpu/payment/v2',)]

def test_graph_guidance_rejects_arbitrary_actions():
    from rca_live_v3.early import GraphGuidedPolicy
    policy=SimpleNamespace(choose=lambda observation:'shell/anything')
    with pytest.raises(ValueError): GraphGuidedPolicy(policy,[]).choose({'history':(),'mask':('STOP','DEFER')})

def test_external_narration_receives_timing_and_distinguishes_cleanup_from_agent_repair(monkeypatch):
    import rca_live_v3.narration as narration
    prompts=[]
    monkeypatch.setenv('LLM_PROVIDER','groq')
    monkeypatch.setenv('GROQ_API_KEY','test-placeholder')
    monkeypatch.setenv('LIVE_NARRATION_MODE','external')
    def post(url,headers,payload):
        prompts.append(payload['messages'][0]['content'])
        return {'choices':[{'message':{'content':'The injector restored the lab. The response took 25.0 seconds.'}}]}
    monkeypatch.setattr(narration,'post_json',post)
    receipt={'id':'private-incident','case':'private-case','status':'COMPLETE','resolved':False,
        'cleanup_confirmed':True,'health':{'confirmed':True},'overall_latency_seconds':25.,
        'diagnosis':{'prediction':'DEFER','confidence':0.},'latency_origin':'injection_requested'}
    described=narration.Narrator().describe(receipt)
    assert described['mode']=='groq' and '25.0 seconds' in described['authoritative']
    assert 'AGENT repair only' in prompts[0] and '"lab_restored": true' in prompts[0]
    assert '"overall_latency_seconds": 25.0' in prompts[0]
    assert 'private-incident' not in prompts[0] and 'private-case' not in prompts[0]
