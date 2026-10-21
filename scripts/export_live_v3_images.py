"""Export the existing lab's exact images and resolved compose for handoff.

Read-only Docker operations. Does not stop, recreate or modify containers.
"""
import argparse,json,hashlib,subprocess
from pathlib import Path

def docker(*args):
    return subprocess.run(['docker',*args],check=True,capture_output=True,text=True).stdout

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('runs/live_v3_images'))
    p.add_argument('--save',action='store_true',help='Also write a Docker image archive for transfer')
    args=p.parse_args()
    if args.output.exists(): raise ValueError('choose a new export directory')
    ids=docker('ps','-aq','--filter','label=com.docker.compose.project=rca-lab').split()
    if not ids: raise ValueError('no existing lab containers')
    rows=json.loads(docker('inspect',*ids))
    services={r['Config']['Labels']['com.docker.compose.service']:r for r in rows}
    required={'payment-path-proxy','front-end','neo4j','loki','promtail','prometheus','payment','carts'}
    if not required<=set(services): raise ValueError('full helper/proxy lab required')
    command=['compose','-p','rca-lab']
    for name in ('docker-compose.yml','configs/lab_pinned.yaml','configs/lab_adaptive_path_v2.yaml','configs/lab_adaptive_helper_images_v2.yaml'):
        command+=['-f',name]
    compose=json.loads(docker(*command,'config','--format','json'))
    if set(compose['services'])!=set(services): raise ValueError('resolved compose differs from deployed service set')
    images={}
    for name,row in services.items():
        compose['services'][name]['image']=row['Image']
        images[name]={'image_id':row['Image'],'container_id':row['Id']}
    args.output.mkdir(parents=True)
    (args.output/'compose.json').write_text(json.dumps(compose,indent=2)+'\n')
    report={'images':images,'owner_application_root':str(Path.cwd().resolve()),
            'archive_included':args.save,'note':'Review and relocate bind mounts before VM launch. Never reuse owner container IDs as VM targets.'}
    if args.save:
        archive=args.output/'images.tar'
        subprocess.run(['docker','image','save','-o',str(archive),*sorted({r['Image'] for r in rows})],check=True)
        with archive.open('rb') as stream: report['archive_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        report['archive_bytes']=archive.stat().st_size
    (args.output/'compose-images.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'archive_included':args.save,'images':len(images)}))

if __name__=='__main__':main()
