"""Incident-scoped Neo4j evidence and existing GraphRAG traversal."""
import json
import os
import re
from neo4j import GraphDatabase
from rca_investigation.contracts import TopologySnapshot,digest
from rca_investigation.contracts_v2 import decode_record
from rca_investigation.backends.replay import ReplayBackend
from .legacy_graph.retriever import get_active_anomalies,dynamic_expansion

class ScopedGraph:
    def __init__(self,driver): self.driver=driver
    def query(self,query,params=None):
        seen=set()
        def scope(match):
            name=match.group(1)
            if name in seen: return '('+name
            seen.add(name)
            return '('+name+':LiveV3Resource'
        query=re.sub(r'\((n|a|b|curr|nbr)(?=[\s:{)])',scope,query)
        with self.driver.session() as session:
            return [dict(row) for row in session.run(query,params or {})]

def anomaly(packet):
    if packet.availability!='OK': return None
    if packet.response_code=='STOPPED': return 'service_crash'
    if packet.response_code=='HIGH': return 'cpu_exhaustion'
    if packet.response_code in ('PRESSURE','PRESSURE_AND_GROWTH'): return 'memory_pressure'
    if packet.response_code=='SLOW': return 'slow_operation'
    if packet.response_code in ('HIGH_HTTP_ERRORS','HIGH_TRANSPORT_FAILURES','MIXED_FAILURES','AUTH_REFUSED','HIGH_APPLICATION_FAILURES'): return 'operation_failure'
    return None

class Graph:
    def __init__(self,expected): self.expected=expected
    def __enter__(self):
        self.driver=GraphDatabase.driver(os.getenv('NEO4J_URI','bolt://localhost:7687'),
            auth=(os.getenv('NEO4J_USER','neo4j'),os.getenv('NEO4J_PASSWORD','password123')))
        self.driver.verify_connectivity()
        self.graph=ScopedGraph(self.driver)
        for name,category in zip(self.expected.resources,self.expected.categories):
            self.graph.query('MERGE (n {name:$name}) SET n.category=$category,n.lab_project="rca-lab",n.role=$category,n.status="unknown"',{'name':name,'category':category})
        # No deletion of the original graph or other demo namespaces.
        self.graph.query('MATCH (a)-[r:CALLS]->(b) DELETE r')
        for caller,callee in self.expected.calls:
            self.graph.query('MATCH (a {name:$a}),(b {name:$b}) MERGE (a)-[:CALLS]->(b)',{'a':caller,'b':callee})
        self.topology=self.read_topology()
        return self
    def __exit__(self,*args): self.driver.close()
    def read_topology(self):
        nodes=self.graph.query('MATCH (n) RETURN n.name AS name,n.category AS category ORDER BY name')
        edges=self.graph.query('MATCH (a)-[:CALLS]->(b) RETURN a.name AS source,b.name AS target ORDER BY source,target')
        value=TopologySnapshot(self.expected.version,tuple(n['name'] for n in nodes),tuple(n['category'] for n in nodes),tuple((e['source'],e['target']) for e in edges))
        if value.hash!=self.expected.hash: raise ValueError('graph dependencies differ from frozen topology')
        return value
    def publish(self,incident,packets):
        self.graph.query('MATCH (n) SET n.anomaly_flag=false,n.anomaly_type=null,n.status="unknown",n.live_incident_id=$id',{'id':incident.incident_id})
        with self.driver.session() as session:
            for packet in packets:
                session.run('MERGE (e:LiveV3Evidence {incident:$id,action:$action}) SET e.payload=$payload,e.hash=$hash',
                    id=incident.incident_id,action=packet.action_id,payload=json.dumps(packet.to_dict()),hash=digest(packet.to_dict())).consume()
                flag=anomaly(packet)
                if flag:
                    self.graph.query('MATCH (n {name:$name}) SET n.anomaly_flag=true,n.anomaly_type=coalesce(n.anomaly_type+",","")+$flag,n.anomaly_since=$time',
                        {'name':packet.resource_id,'flag':flag,'time':packet.sample_time})
                if packet.action_id.startswith('status/') and packet.availability=='OK':
                    self.graph.query('MATCH (n {name:$name}) SET n.status=$status',{'name':packet.resource_id,'status':packet.response_code.lower()})
        rows=get_active_anomalies(self.graph)
        return {'source':'neo4j_existing_graphrag','dependencies':self.read_topology().to_dict(),
            'anomalies':rows,'traversal':[dynamic_expansion(self.graph,r['name']) for r in rows],
            'candidate_roots_are_hypotheses':True}
    def clear(self,identifier):
        self.graph.query('MATCH (n {live_incident_id:$id}) SET n.anomaly_flag=false,n.anomaly_type=null,n.status="running"',{'id':identifier})
    def backend(self,specs,incident): return PaidGraphBackend(self.driver,specs,incident)

class PaidGraphBackend(ReplayBackend):
    def __init__(self,driver,specs,incident):
        self.driver=driver
        with driver.session() as session:
            records=list(session.run('MATCH (e:LiveV3Evidence {incident:$id}) RETURN e.payload AS payload,e.hash AS hash',id=incident.incident_id))
        packets=[]
        for row in records:
            payload=json.loads(row['payload'])
            if payload.get('source')=='live_v3_prefix_r1' and payload.get('schema_version')==2:
                from .short_packets import ShortPacket
                packet=ShortPacket.from_dict(payload)
            else:
                packet=decode_record(payload,'packet')
            if digest(packet.to_dict())!=row['hash']: raise ValueError('graph packet checksum changed')
            packets.append(packet)
        super().__init__(packets,specs)

    def execute(self,spec):
        if self._incident is None or self._specs.get(spec.action_id)!=spec:
            raise ValueError('unapproved or inactive graph probe')
        packet=self._packets[spec.action_id]
        # Short live packets retain their separate duration-aware validator.
        packet.validate_context(spec,self._incident)
        return packet
