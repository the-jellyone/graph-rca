"""Deployment inventory and bounded transactions, separate from benchmark provenance."""
import json
from pathlib import Path
import yaml
from rca_investigation.dataset import atomic_json
from rca_investigation.contracts import digest
from rca_investigation.faults import LabDriver
from rca_investigation.faults_v3 import LabDriverV3
from .assets import checked_record,sha

def quota(host):
    return host.get('NanoCpus',0)/1e9 or host.get('CpuQuota',0)/(host.get('CpuPeriod',0) or 100000)

def inventory(root,assets,destination):
    destination=Path(destination)
    if destination.exists(): raise ValueError('deployment registry already exists')
    driver=LabDriver(assets.topology,root)
    driver.resolve()
    rows=json.loads(driver.command(['inspect',*driver.ids.values()])[0])
    original=yaml.safe_load((assets.root/'training-registry.yaml').read_text())['resources']
    resources={}
    for row in rows:
        resource=row['Config']['Labels']['com.docker.compose.service']
        if resource not in original: continue
        old=original[resource]
        if quota(row['HostConfig'])!=old['cpu_quota_cores'] or row['HostConfig']['Memory']!=old['memory_limit_bytes']:
            raise ValueError('deployment resource limits differ from training: '+resource)
        resources[resource]={**old,'container_id':row['Id'],'container_name':row['Name'],
            'log_selector':'{container='+json.dumps(row['Name'])+'}','image_id':row['Image']}
    # Proxy exists on this deployment and has its own recorded identity.
    from rca_investigation.faults_v2 import AdaptiveLabDriver
    proxy=AdaptiveLabDriver(assets.topology,root)
    state=proxy.proxy_control()
    if state['delay_ms']!=0: raise ValueError('proxy has an active unowned delay')
    result={'protocol':'live_v3_window120_r1','project':'rca-lab','resources':resources,
            'proxy_id':state['receipt']['argv'][1],'bundle_hash':assets.manifest['hash']}
    result['hash']=digest(result)
    atomic_json(destination,result)
    return result

class DemoDriver(LabDriverV3):
    def __init__(self,root,assets,registry_path):
        self.deployment=checked_record(registry_path)
        if self.deployment['bundle_hash']!=assets.manifest['hash']:
            raise ValueError('registry belongs to another inference export')
        self.assets=assets
        super().__init__(assets.topology,root,self.deployment['resources'])

    def resolve(self):
        result=LabDriver.resolve(self)
        if any(result[r]!=v['container_id'] for r,v in self.registry.items()):
            raise ValueError('live container identity changed; declare a new deployment registry')
        rows=json.loads(self.command(['inspect',*(result[r] for r in self.registry)])[0])
        for row in rows:
            resource=row['Config']['Labels']['com.docker.compose.service']
            item=self.registry[resource]
            if row['Image']!=item['image_id'] or row['HostConfig']['Memory']!=item['memory_limit_bytes'] or quota(row['HostConfig'])!=item['cpu_quota_cores']:
                raise ValueError('live image/resource limits changed')
        self.proxy_control()
        return result

    def proxy_control(self,delay=None):
        value=super().proxy_control()
        if value['receipt']['argv'][1]!=self.deployment['proxy_id']:
            raise ValueError('live proxy identity changed')
        return super().proxy_control(delay) if delay is not None else value

    def inject_resource_lease(self,resource,mode,lease,seconds=210,allocation_bytes=0):
        if (resource,mode) not in {('payment','cpu'),('carts','memory')} or seconds!=210:
            raise ValueError('unapproved live helper')
        limit=self.registry[resource]['memory_limit_bytes']
        if mode=='cpu' and allocation_bytes!=0 or mode=='memory' and not 0<allocation_bytes<=min(512*1024**2,limit*.5):
            raise ValueError('unbounded allocation')
        if self.state(resource)['state']!='running': raise ValueError('helper target stopped')
        current=self.command(['exec',self.ids[resource],'sh','-c','if [ -f /tmp/rca-helper.pid ]; then cat /tmp/rca-helper.pid; fi'])[0]
        if current.strip(): raise ValueError('existing helper is not owned by this transaction')
        self.helper_leases[resource]=lease
        self.command(['cp',str(self.assets.root/'helper'),self.ids[resource]+':/tmp/rca-helper'])
        _,receipt=self.command(['exec','-d',self.ids[resource],'/tmp/rca-helper','-mode',mode,
            '-seconds',str(seconds),'-workers','8','-bytes',str(allocation_bytes),'-lease',lease])
        return {**receipt,'helper_sha256':sha(self.assets.root/'helper'),'lease':lease,'container_id':self.ids[resource]}
