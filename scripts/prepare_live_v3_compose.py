"""Relocate owner bind mounts to a VM application directory, without launching it."""
import argparse,json
from pathlib import Path,PureWindowsPath,PurePosixPath

def relocate(compose,owner_root,new_root):
    cls=PureWindowsPath if ':' in owner_root else PurePosixPath
    owner=cls(owner_root)
    result=json.loads(json.dumps(compose))
    for service in result['services'].values():
        for mount in service.get('volumes',[]):
            if mount.get('type')!='bind':continue
            value=mount['source']
            if value in ('/var/run/docker.sock','/var/lib/docker/containers'):continue
            try: relative=cls(value).relative_to(owner)
            except ValueError as exc:raise ValueError('unregistered owner bind mount: '+value) from exc
            if '..' in relative.parts:raise ValueError('bind mount escapes application root')
            mount['source']=str(Path(new_root).joinpath(*relative.parts))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True,help='Directory from export_live_v3_images.py')
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--output',type=Path,default=Path('runs/live_v3/compose-vm.json'))
    args=p.parse_args()
    if args.output.exists():raise ValueError('preserve the previous deployment compose')
    metadata=json.loads((args.input/'compose-images.json').read_text())
    compose=json.loads((args.input/'compose.json').read_text())
    value=relocate(compose,metadata['owner_application_root'],args.root.resolve())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,indent=2)+'\n')
    print('Prepared '+str(args.output)+'. Review binds, ports and permissions before starting the VM lab.')
if __name__=='__main__':main()
