"""Verify a C0 archive offline, without importing the EMS or connecting to VMs."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, description):
    if not condition:
        raise ValueError(description)


def verify_archive(path, host):
    require(digest(path) == host['archive_sha256'], 'Archive checksum: ' + path.name)
    with tarfile.open(path, 'r:gz') as archive:
        members = {'files/' + name.lstrip('/'): info for name, info in host['files'].items()}
        members.update(host['generated'])
        for name, info in members.items():
            with archive.extractfile(name) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == info['sha256'],
                        'Member checksum: ' + name)
    return len(members)


def verify(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    require(manifest['status'] == 'CAPTURED', 'Incomplete baseline capture')
    with zipfile.ZipFile(directory / 'workspace.zip') as archive:
        for name, info in manifest['local_files'].items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == info['sha256'],
                    'Source checksum: ' + name)
    hosts = {}
    for label, host in manifest['hosts'].items():
        require(not host['errors'], 'Capture reported errors: ' + label)
        hosts[label] = verify_archive(directory / (label + '.tar.gz'), host)
    for path in directory.glob('*-supplement.json'):
        host = json.loads(path.read_text(encoding='utf-8'))
        require(not host['errors'], 'Supplement reported errors: ' + path.name)
        hosts[path.stem] = verify_archive(path.with_suffix('.tar.gz'), host)
    db = manifest.get('ems_database')
    if db:
        snapshot = Path(db.get('snapshot', directory / 'ems.sqlite3')).resolve()
        # Never accidentally validate or lock the current production database.
        require(snapshot.is_relative_to(directory.parent.resolve()), 'Snapshot outside private evidence root')
        require(digest(snapshot) == db['sha256'], 'EMS snapshot checksum')
        conn = sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)
        try:
            require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'EMS snapshot integrity')
        finally:
            conn.close()
    return {'verified': True, 'source_files': len(manifest['local_files']),
            'archive_members': hosts, 'ems_integrity': 'ok' if db else 'not_captured',
            'operational_restore_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory.resolve()), indent=2))
