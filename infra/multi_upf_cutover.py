"""Transactional triad cutover. Private backups and independent rollback timers.

Run from backend using its environment: python ../infra/multi_upf_cutover.py --execute.
Only slice data changes in MongoDB. SIM secrets, SQN and CHF balances are untouched.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from uuid import uuid4
import yaml

from multi_upf_stage import ROOT, active_config, candidates
from app.core.config import get_settings
from e2e_native import Lab

SLICES = [{'sst': n, 'sd': f'{n:06x}'} for n in (1, 2, 3)]
RADIO_SLICES = [{'sst': n, 'sd': n} for n in (1, 2, 3)]
CLI = '/home/emsadmin/UERANSIM/build/nr-cli'
UE_CONFIG = '/home/emsadmin/UERANSIM/config/open5gs-ue.yaml'
GNB_CONFIG = '/home/emsadmin/UERANSIM/config/open5gs-gnb.yaml'
SUPI = 'imsi-999700000000001'


def wait_for(check, description, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(description)


def session_number(label):
    match = re.fullmatch(r'(?:PDU Session)?(\d+)', str(label))
    if not match or not 1 <= int(match[1]) <= 15:
        raise ValueError('Unexpected UERANSIM session label')
    return int(match[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.parse_args()
    settings = get_settings()
    hosts = {n: Lab(settings, p) for n, p in [('core', 2222), ('upf', 2223), ('gnb', 2225), ('ue', 2226)]}
    core, upf, gnb, ue = (hosts[n] for n in ('core', 'upf', 'gnb', 'ue'))
    tag = 'maestro-triad-' + uuid4().hex[:10]
    local = ROOT / '.work' / tag
    local.mkdir()
    stage, changes, timers = {}, {}, []
    result = {'status': 'PREPARING', 'sessions': {}, 'xdp_acceleration': False}
    mutated = False
    try:
        assert ue.run([CLI, '--dump']).split() == [SUPI], 'Only the primary UE may be active'
        for name, host in hosts.items():
            stage[name] = host.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
            changes[name] = []

        def prepare(name, path, content, mode='640'):
            host = hosts[name]
            index = len(changes[name])
            original, candidate = stage[name] + f'/original-{index}', stage[name] + f'/candidate-{index}'
            exists = host.run(['sh', '-c', 'test -e ' + shlex.quote(path) + ' && echo yes'], check=False).strip() == 'yes'
            if exists:
                host.run(['cp', '-a', path, original], sudo=True)
                (local / (name + '-' + str(index) + '.original')).write_text(host.run(['cat', path], sudo=True))
            host.write(candidate, content)
            (local / (name + '-' + str(index) + '.candidate')).write_text(content)
            changes[name].append((path, original if exists else None, candidate, mode))

        binary, _, _, smf2 = active_config(core, 'open5gs-smfd2')
        _, _, _, upf_config = active_config(upf, 'open5gs-upfd-urllc')
        smf3, _ = candidates(smf2, upf_config)
        assert smf3['smf']['chf']['enabled'] and upf_config['upf']['charging_enforcement']
        core.run(['test', '!', '-e', '/etc/open5gs/smf3.yaml'], sudo=True)
        assert '10.210.50.18' not in core.run(['ip', '-j', 'address'])
        assert 'maestro-mec' not in upf.run(['ip', 'netns', 'list'], sudo=True)
        for host in (core, gnb):
            assert not json.loads(host.run(['ip', '-j', 'route', 'show', 'exact', '10.210.50.22/32']))
        env = core.run(['systemctl', 'show', 'open5gs-smfd2', '-p', 'Environment', '--value'])
        lib = re.search(r'(?:^|\s)LD_LIBRARY_PATH=([^\s"\']+)', env)
        assert lib
        owner = smf3['smf']['chf']['nf_instance_id']
        token = secrets.token_hex(32)
        chf_unit = core.run(['systemctl', 'cat', 'open5gs-chfd'], sudo=True)
        auth_paths = re.findall(r'^EnvironmentFile=-?(\S+)', chf_unit, re.M)
        auth = {}
        for path in auth_paths:
            for line in core.run(['cat', path], sudo=True).splitlines():
                if line.startswith('CHF_SBI_TOKENS='):
                    auth = json.loads(shlex.split(line.split('=', 1)[1])[0])
        assert len(auth) >= 2 and owner not in auth
        auth[owner] = token
        prepare('core', '/etc/open5gs/smf3.yaml', yaml.safe_dump(smf3, sort_keys=False))
        prepare('core', '/etc/open5gs/smf3.env', 'SMF_CHF_TOKEN=' + token + '\n', '600')
        prepare('core', '/etc/open5gs/chf-triad.env', "CHF_SBI_TOKENS='" + json.dumps(auth) + "'\n", '600')
        prepare('core', '/etc/systemd/system/open5gs-chfd.service.d/95-maestro-triad.conf',
                '[Service]\nEnvironmentFile=/etc/open5gs/chf-triad.env\n', '644')
        unit = f'''[Unit]
Description=MAEstro SMF 3 (URLLC 2/000002, isolated PFCP)
Wants=network-online.target
After=network-online.target open5gs-nrfd.service open5gs-scpd.service
[Service]
Type=simple
User=open5gs
Group=open5gs
ExecStartPre=+/usr/sbin/ip address replace 10.210.50.18/32 dev enp0s8
ExecStartPre=+/usr/sbin/ip route replace 10.210.50.22/32 via 10.210.50.8
ExecStart={binary} -c /etc/open5gs/smf3.yaml
Environment=LD_LIBRARY_PATH={lib[1]}
EnvironmentFile=/etc/open5gs/smf3.env
Restart=on-failure
RestartSec=3
RestartPreventExitStatus=1
MemoryMax=512M
[Install]
WantedBy=multi-user.target
'''
        prepare('core', '/etc/systemd/system/open5gs-smfd3.service', unit, '644')
        for path, key in [('/etc/open5gs/amf.yaml', 'amf'), ('/etc/open5gs/nssf.yaml', 'nssf'), ('/etc/open5gs/smf2.yaml', 'smf')]:
            cfg = yaml.safe_load(core.run(['cat', path], sudo=True))
            if key == 'amf':
                for plmn in cfg['amf']['plmn_support']:
                    plmn['s_nssai'] = deepcopy(SLICES)
            elif key == 'nssf':
                client = cfg['nssf']['sbi']['client']
                client['nsi'] = [dict(client['nsi'][0], s_nssai=s) for s in SLICES]
            else:
                cfg['smf']['info'] = [{'s_nssai': [dict(SLICES[2], dnn=['corporate'])]}]
            prepare('core', path, yaml.safe_dump(cfg, sort_keys=False))
        for name, path in [('gnb', GNB_CONFIG), ('ue', UE_CONFIG)]:
            cfg = yaml.safe_load(hosts[name].run(['cat', path], sudo=True))
            if name == 'gnb':
                cfg['slices'] = RADIO_SLICES
            else:
                assert cfg['supi'] == SUPI
                cfg['configured-nssai'] = RADIO_SLICES
                cfg['default-nssai'] = [RADIO_SLICES[0]]
                cfg['sessions'] = [{'type': 'IPv4', 'apn': 'internet', 'slice': RADIO_SLICES[0]}]
            prepare(name, path, yaml.safe_dump(cfg, sort_keys=False), '644')
        prepare('gnb', '/etc/systemd/system/ueransim-gnb.service.d/95-maestro-triad.conf',
                '[Service]\nExecStartPre=+/usr/sbin/ip route replace 10.210.50.22/32 via 10.210.50.8\n', '644')

        # Dedicated N6 endpoint; it cannot route to the other slices or the Internet.
        mec_start = '''ip netns add maestro-mec
ip link add murllc-mec type veth peer name mec0
ip link set murllc-mec netns maestro-urllc
ip link set mec0 netns maestro-mec
ip -n maestro-urllc address add 172.31.48.1/30 dev murllc-mec
ip -n maestro-urllc link set murllc-mec up
ip -n maestro-mec link set lo up
ip -n maestro-mec address add 172.31.48.2/30 dev mec0
ip -n maestro-mec link set mec0 up
ip -n maestro-mec route add 10.47.0.0/16 via 172.31.48.1
ip netns exec maestro-urllc nft add rule inet maestro_urllc forward iifname ogstun oifname murllc-mec ip saddr 10.47.0.0/16 ip daddr 172.31.48.2 accept
ip netns exec maestro-urllc nft add rule inet maestro_urllc forward iifname murllc-mec oifname ogstun ip saddr 172.31.48.2 ip daddr 10.47.0.0/16 accept
'''
        mec_stop = 'ip -n maestro-urllc link del murllc-mec 2>/dev/null || true\nip netns del maestro-mec 2>/dev/null || true\n'
        netpath = '/usr/local/libexec/maestro-urllc-network'
        network = upf.run(['cat', netpath], sudo=True)
        assert '\nNFT\n' in network
        prepare('upf', netpath, network.replace('\nNFT\n', '\nNFT\n' + mec_start).replace('stop)\n', 'stop)\n' + mec_stop), '750')
        upf.write(stage['upf'] + '/mec-start.sh', '#!/bin/sh\nset -eu\n' + mec_start, 0o700)
        upf.write(stage['upf'] + '/mec-stop.sh', '#!/bin/sh\n' + mec_stop, 0o700)
        original_rules = upf.run(['ip','netns','exec','maestro-urllc','nft','list','table','inet','maestro_urllc'], sudo=True)
        upf.write(stage['upf'] + '/nft-original', original_rules)

        mongo = 'db.getSiblingDB("open5gs").subscribers'
        selector = json.dumps({'imsi': SUPI[5:]})
        js = ('const fs=require("fs");const c=' + mongo + ';const d=c.findOne(' + selector + ');'
              'if(!d)throw Error("Subscriber missing");'
              'fs.writeFileSync(' + json.dumps(stage['core'] + '/slice.ejson') + ',EJSON.stringify(d.slice),{mode:384});'
              'const sessions=d.slice.flatMap(s=>s.session||[]);'
              'const internet=sessions.filter(s=>s.name==="internet"),corp=sessions.filter(s=>s.name==="corporate");'
              'if(internet.length!==1||corp.length!==1)throw Error("Ambiguous DNNs");'
              'const urllc=EJSON.parse(EJSON.stringify(internet[0]));urllc.name="5g-plus";urllc._id=new ObjectId();'
              'const slices=[internet[0],urllc,corp[0]].map((s,i)=>({sst:i+1,sd:"00000"+(i+1),default_indicator:i===0,session:[s],_id:new ObjectId()}));'
              'fs.writeFileSync(' + json.dumps(stage['core'] + '/new-slice.ejson') + ',EJSON.stringify(slices),{mode:384});')
        core.write(stage['core'] + '/prepare.js', js)
        core.run(['mongosh', '--quiet', '--file', stage['core'] + '/prepare.js'])
        (local/'slice.ejson').write_bytes(core.read(stage['core']+'/slice.ejson'))
        for action, file in [('apply','new-slice.ejson'),('restore','slice.ejson')]:
            js = ('const fs=require("fs");const r=' + mongo + '.updateOne(' + selector + ',{$set:{slice:EJSON.parse(fs.readFileSync(' + json.dumps(stage['core']+'/'+file) + ',"utf8"))}});if(r.matchedCount!==1)throw Error("Missing subscriber");')
            core.write(stage['core']+'/'+action+'.js', js)

        for name, host in hosts.items():
            rollback = '#!/bin/sh\nset -eu\n'
            if name == 'core':
                rollback += 'systemctl disable --now open5gs-smfd3.service || true\n'
            for path, backup, _, _ in changes[name]:
                rollback += shlex.join(['cp','-a',backup,path] if backup else ['rm','-f',path]) + '\n'
            rollback += 'systemctl daemon-reload\n'
            if name == 'core':
                rollback += shlex.join(['mongosh','--quiet','--file',stage[name]+'/restore.js']) + '\n'
                rollback += 'ip address del 10.210.50.18/32 dev enp0s8 2>/dev/null || true\nip route del 10.210.50.22/32 via 10.210.50.8 2>/dev/null || true\n'
                rollback += 'systemctl restart open5gs-chfd open5gs-nssfd open5gs-smfd2 open5gs-amfd\n'
            elif name == 'upf':
                rollback += shlex.join(['/bin/sh',stage[name]+'/mec-stop.sh']) + '\n'
                rollback += 'ip netns exec maestro-urllc nft delete table inet maestro_urllc\n'
                rollback += shlex.join(['ip','netns','exec','maestro-urllc','nft','-f',stage[name]+'/nft-original']) + '\n'
            else:
                if name == 'gnb':
                    rollback += 'ip route del 10.210.50.22/32 via 10.210.50.8 2>/dev/null || true\n'
                rollback += 'systemctl restart ueransim-' + name + '\n'
            host.write(stage[name]+'/rollback.sh',rollback,0o700)
            timer = tag+'-'+name
            host.run(['systemd-run','--unit='+timer,'--on-active='+str(600+30*list(hosts).index(name))+'s','/bin/sh',stage[name]+'/rollback.sh'],sudo=True)
            timers.append((name,timer))
        result['remote_evidence'] = stage
        (local/'result.json').write_text(json.dumps(result,indent=2))
        print('Backups saved; rollback watchdogs armed', flush=True)
        mutated = True
        # Graceful deregistration settles active CHF reservations before service restarts.
        ue.run([CLI,SUPI,'-e','deregister normal'],check=False)
        time.sleep(2)
        ue.run(['systemctl','stop','ueransim-ue'],sudo=True)
        for name, host in hosts.items():
            for path, backup, candidate, mode in changes[name]:
                if backup:
                    host.run(['cp',candidate,path],sudo=True)
                else:
                    host.run(['install','-D','-o','root','-g','open5gs' if name=='core' else 'root','-m',mode,candidate,path],sudo=True)
            host.run(['systemctl','daemon-reload'],sudo=True)
        core.run(['install','-d','-o','open5gs','-g','open5gs','-m','700','/var/lib/open5gs/chf-journal-smf3'],sudo=True)
        core.run(['systemctl','restart','open5gs-chfd'],sudo=True)
        core.run(['systemctl','start','open5gs-smfd3'],sudo=True)
        smf3_pid = core.run(['systemctl','show','open5gs-smfd3','-p','MainPID','--value']).strip()
        def associated():
            return 'PFCP associated [10.210.50.22]' in core.run(['journalctl','_PID='+smf3_pid,'-b','-n','200','--no-pager'],sudo=True)
        wait_for(associated,'SMF3 PFCP association failed')
        print('SMF3 associated exclusively with URLLC UPF',flush=True)
        upf.run(['/bin/sh',stage['upf']+'/mec-start.sh'],sudo=True)
        core.run(['mongosh','--quiet','--file',stage['core']+'/apply.js'],sudo=True)
        core.run(['systemctl','restart','open5gs-nssfd','open5gs-smfd2','open5gs-amfd'],sudo=True)
        gnb.run(['systemctl','restart','ueransim-gnb'],sudo=True)
        time.sleep(3)
        ue.run(['systemctl','start','ueransim-ue'],sudo=True)

        def pdu(dnn):
            raw=ue.run([CLI,SUPI,'-e','ps-list'],check=False)
            try: parsed=yaml.safe_load(raw)
            except yaml.YAMLError:return None
            if not isinstance(parsed,dict):return None
            for key,row in parsed.items():
                if isinstance(row,dict) and row.get('apn')==dnn and row.get('state')=='PS-ACTIVE':
                    return dict(row,session_id=session_number(key))
            return None
        wait_for(lambda:pdu('internet'),'Internet session did not recover',60)
        time.sleep(3)
        for dnn,sst,prefix in [('5g-plus',2,'10.47.'),('corporate',3,'10.46.')]:
            output=ue.run([CLI,SUPI,'-e',f'ps-establish IPv4 --sst {sst} --sd {sst} --dnn {dnn}'])
            (local/(dnn+'-cli.txt')).write_text(output)
            session=wait_for(lambda:pdu(dnn),'PDU session failed: '+dnn,45)
            assert session['address'].startswith(prefix) and session['s-nssai']=={'sst':sst,'sd':sst},session
            result['sessions'][dnn]=session
            links=json.loads(ue.run(['ip','-j','-4','address']))
            iface=next(i['ifname'] for i in links if any(a['local']==session['address'] for a in i['addr_info']))
            destination='172.31.48.2' if dnn=='5g-plus' else '10.46.0.1'
            result['sessions'][dnn]['ping']=ue.run(['ping','-I',iface,'-c','3','-W','2',destination])
            if dnn=='5g-plus':
                result['sessions'][dnn]['reverse_ping']=upf.run(['ip','netns','exec','maestro-mec','ping','-I','mec0','-c','3','-W','2',session['address']],sudo=True)
            print(dnn,session['address'],'S-NSSAI and connectivity verified',flush=True)
        result['sessions']['internet']=pdu('internet')
        # Return to the initial, single internet profile for EMS operation.
        for dnn in ('5g-plus','corporate'):
            ue.run([CLI,SUPI,'-e','ps-release '+str(result['sessions'][dnn]['session_id'])])
        wait_for(lambda: pdu('internet') and not pdu('5g-plus') and not pdu('corporate'),
                 'Could not restore single internet session')
        for unit in ['open5gs-smfd','open5gs-smfd2','open5gs-smfd3','open5gs-amfd','open5gs-udmd','open5gs-chfd']:
            assert core.run(['systemctl','is-active',unit]).strip()=='active'
        core.run(['systemctl','enable','open5gs-smfd3'],sudo=True)
        upf.run(['systemctl','enable','maestro-urllc-network','open5gs-upfd-urllc'],sudo=True)
        result['status']='PASS'
        for name,timer in timers:
            hosts[name].run(['systemctl','stop',timer+'.timer'],sudo=True)
    except BaseException:
        result['status']='ROLLBACK_REQUIRED' if mutated else 'NOT_APPLIED'
        restored=True
        if mutated:
            for name in ('core','upf','gnb','ue'):
                try:hosts[name].run(['/bin/sh',stage[name]+'/rollback.sh'],sudo=True,timeout=60)
                except Exception:restored=False
        if restored:
            result['status']='ROLLED_BACK' if mutated else 'NOT_APPLIED'
            for name,timer in timers:
                hosts[name].run(['systemctl','stop',timer+'.timer'],sudo=True,check=False)
        raise
    finally:
        result['remote_evidence']=stage
        (local/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({'status':result['status'],'evidence':str(local)}),flush=True)
        for host in hosts.values():host.client.close()


if __name__=='__main__':main()
