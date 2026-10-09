"""Audited, graceful VirtualBox resize; explicit Core allocation required."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/c8-campaign/ieee-2vcpu/setup'
VBOX = r'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
VMS = [('EMS-Testbed-4G5G', 2222), ('EMS-UPF-01', 2223),
       ('EMS-UPF-02', 2224), ('EMS-GNB-01', 2225), ('EMS-UE-01', 2226)]


def command(*args):
    r = subprocess.run([VBOX, *args], capture_output=True, text=True)
    with (OUT / 'hardware-commands.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(),
                            'argv': list(args), 'returncode': r.returncode,
                            'stdout': r.stdout, 'stderr': r.stderr}) + '\n')
    r.check_returncode()
    return r.stdout


def info(vm):
    raw = command('showvminfo', vm, '--machinereadable')
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def resize(core_cpus):
    assert json.loads((OUT / 'integrity-reconciled.json').read_text())['success']
    path = OUT / 'hardware-transition.json'
    assert not path.exists(), 'No silent repeat of hardware transition'
    record = {'started_at': datetime.now(timezone.utc).isoformat(),
              'status': 'started', 'core_cpus': core_cpus, 'before': {}, 'after': {}}
    def save():
        path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    for vm, _ in VMS:
        state = info(vm)
        record['before'][vm] = state
        cfg = Path(json.loads(state['CfgFile']))
        shutil.copyfile(cfg, OUT / (vm + '-original.vbox'))
    save()
    try:
        for vm, _ in reversed(VMS):
            if info(vm)['VMState'] == '"running"':
                command('controlvm', vm, 'acpipowerbutton')
                deadline = time.monotonic() + 120
                while info(vm)['VMState'] != '"poweroff"':
                    if time.monotonic() > deadline:
                        raise RuntimeError('Graceful shutdown timed out: ' + vm)
                    time.sleep(2)
            print(json.dumps({'vm': vm, 'state': 'poweroff'}), flush=True)
        for vm, _ in VMS:
            cpus = core_cpus if vm == VMS[0][0] else (1 if vm == 'EMS-UPF-02' else 2)
            command('modifyvm', vm, '--cpus', str(cpus))
            assert info(vm)['cpus'] == str(cpus)
            print(json.dumps({'vm': vm, 'configured_vcpus': cpus}), flush=True)
        from c8_remote import LoggedLab, get_settings
        for vm, port in VMS:
            command('startvm', vm, '--type', 'headless')
            deadline = time.monotonic() + 180
            while True:
                try:
                    host = LoggedLab(get_settings(), port, OUT / 'boot')
                    try:
                        online = host.run(['getconf', '_NPROCESSORS_ONLN']).strip()
                    finally:
                        host.client.close()
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(2)
            record['after'][vm] = {'vcpus_online': int(online), 'state': info(vm)['VMState']}
            save()
            print(json.dumps({'vm': vm, **record['after'][vm]}), flush=True)
        record['status'] = 'completed'
    except BaseException as exc:
        record['status'] = 'failed'
        record['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-cpus', type=int, choices=[1, 8], required=True)
    resize(parser.parse_args().core_cpus)
