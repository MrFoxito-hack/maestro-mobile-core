"""Freeze C8 artifact inventory, include reproducible code, verify SHA-256."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');p.add_argument('--zip',action='store_true')
    p.add_argument('--root',type=Path,default=ROOT/'.work/c8-campaign');a=p.parse_args()
    root=a.root.resolve();evidence=root/'evidence';evidence.mkdir(exist_ok=True)
    manifest=evidence/'inventory.json'
    seal=evidence/'inventory.sha256'
    verification=evidence/'verification.json'
    excluded={manifest,seal,verification}
    if not a.verify:
        assert json.loads((evidence/'certification-status.json').read_text(encoding='utf-8'))['accepted'], 'C8_not_certified'
        release_path=evidence/'campaign-index.json'
        release_metadata=json.loads(release_path.read_text()) if release_path.exists() else {}
        extension=root/release_metadata.get('urllc_directory','ieee')
        extended_certified=(extension/'evidence/certification-status.json').exists() and (evidence/'campaign-index.json').exists()
        if extended_certified:
            extended_status=json.loads((extension/'evidence/certification-status.json').read_text(encoding='utf-8'))
            assert extended_status['accepted'] and extended_status['formal_trials']=={'urllc':40}, 'extended_C8_not_certified'
            assert extended_status['formal_packets']==60000
            release=json.loads((evidence/'campaign-index.json').read_text())
            assert release['accepted'] and release['formal_trials']=={'miot':24,'isolation':12,'urllc':40}
            assert release['formal_packets']==147720 and release['no_pooling_preliminary_and_extended_urllc']
            (extension/'evidence/datasets').mkdir(exist_ok=True)
            for f in (extension/'datasets').glob('*.csv'):shutil.copyfile(f,extension/'evidence/datasets'/f.name)
            backup=ROOT/'.work/c8-campaign-baseline-v1-certified.zip'
            assert sha(backup)=='ea40608970a94218875a880e5ed824c0b8e2d53286463a35f58f3e91d7bba3b7'
        (evidence/'datasets').mkdir(exist_ok=True)
        for f in (root/'datasets').glob('*.csv'):shutil.copyfile(f,evidence/'datasets'/f.name)
        (root/'source').mkdir(exist_ok=True)
        for f in (ROOT/'infra').glob('c8_*.py'):shutil.copyfile(f,root/'source'/f.name)
        (root/'documents').mkdir(exist_ok=True)
        for f in (ROOT/'docs').glob('C8*.md'):shutil.copyfile(f,root/'documents'/f.name)
        shutil.copyfile(ROOT/'docs/PLAN_CIERRE_TESIS_MAESTRO_5G.md',root/'documents/PLAN_CIERRE_TESIS_MAESTRO_5G.md')
        (root/'source/tests').mkdir(exist_ok=True)
        for f in (ROOT/'infra/tests').glob('test_c8_*.py'):shutil.copyfile(f,root/'source/tests'/f.name)
        status=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)
        (root/'git-status.txt').write_text(status,encoding='utf-8')
        files=[{'path':f.relative_to(root).as_posix(),'bytes':f.stat().st_size,'sha256':sha(f)}
               for f in sorted(root.rglob('*')) if f.is_file() and f not in excluded and '__pycache__' not in f.parts]
        oe4_path=evidence/'oe4-acceptance.json'
        if oe4_path.exists():
            assert json.loads(oe4_path.read_text())['accepted'], 'OE4_performance_not_accepted'
        data={'schema':1,'package_id':'c8-oe3-rr-n20' if extension.name=='ieee-rr' and extended_certified else ('c8-20261007-oe4-qos' if oe4_path.exists() else ('c8-20261007-v2-n20' if extended_certified else 'c8-20261007')),'created_at':datetime.now(timezone.utc).isoformat(),
              'additional_ieee_scope':'certified' if extended_certified else ('retained but not certified by this release' if extension.exists() else 'absent'),
              'additional_ieee_directory':extension.relative_to(root).as_posix(),
              'git_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'working_tree_dirty':bool(status.strip()),'git_sha_is_not_full_workspace_identity':True,
              'source_identity':'Per-run executed source snapshots and SHA-256, plus package source/',
              'files':files}
        manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        seal.write_text(sha(manifest)+'  inventory.json\n',encoding='ascii')
    if seal.read_text().split()[0]!=sha(manifest):raise ValueError('inventory_seal_mismatch')
    data=json.loads(manifest.read_text(encoding='utf-8'))
    actual={f.relative_to(root).as_posix() for f in root.rglob('*')
            if f.is_file() and f not in excluded and '__pycache__' not in f.parts}
    if actual!={row['path'] for row in data['files']}:raise ValueError('inventory_file_set_mismatch')
    for row in data['files']:
        path=(root/row['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha(path)!=row['sha256'] or path.stat().st_size!=row['bytes']:
            raise ValueError('manifest_mismatch:'+row['path'])
    result={'verified_files':len(data['files']),'manifest_sha256':sha(manifest),'verified_at':datetime.now(timezone.utc).isoformat()}
    archive=root.parent/('c8-campaign-oe3-rr-reproducibility.zip' if data['package_id']=='c8-oe3-rr-n20' else 'c8-campaign-reproducibility.zip')
    if a.verify and archive.exists():
        with zipfile.ZipFile(archive) as z:
            assert hashlib.sha256(z.read('c8-campaign/evidence/inventory.json')).hexdigest()==sha(manifest), 'ZIP_inventory_mismatch'
            for row in data['files']:
                with z.open('c8-campaign/'+row['path']) as stream:
                    assert hashlib.file_digest(stream,'sha256').hexdigest()==row['sha256'], 'ZIP_member_mismatch:'+row['path']
        result.update(archive=str(archive),archive_sha256=sha(archive),archive_members_verified=True)
    verification.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    if a.zip:
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for f in excluded:z.write(f,'c8-campaign/'+f.relative_to(root).as_posix())
            for row in data['files']:z.write(root/row['path'],'c8-campaign/'+row['path'])
        result.update(archive=str(archive),archive_sha256=sha(archive))
    print(json.dumps(result))


if __name__=='__main__':main()
