"""Certify the fixed real-time OE3 campaign without selecting favorable runs."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/c8-campaign'


def dump(path,data):
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def seal_previous(root):
    path=root/'setup/previous-n20-sha256.json'
    assert not path.exists(), 'previous_seal_already_exists'
    previous=root.parent/'ieee'
    files={p.relative_to(previous).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(previous.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    assert files and json.loads((previous/'setup/acquisition.json').read_text())['status']=='completed'
    dump(path,{'directory':'ieee','created_at':datetime.now(timezone.utc).isoformat(),'files':files})
    print(json.dumps({'preserved_previous_files':len(files)}))


def progress(root):
    acquisition=json.loads((root/'setup/acquisition.json').read_text())
    totals={'kernel':{'attempted':0,'ack':0},'xdp':{'attempted':0,'ack':0}}
    for path in sorted((root/'runs').glob('*/campaign.json')):
        campaign=json.loads(path.read_text())
        for trial in campaign['trials']:
            if trial['config']['warmup']:continue
            raw=json.loads((path.parent/(trial['name']+'-raw.json')).read_text())
            for stream in raw['streams']:
                tally=totals[trial['config']['mode']]
                tally['attempted']+=len(stream['packets'])
                tally['ack']+=sum(p['ack'] for p in stream['packets'])
    print(json.dumps({'status':acquisition['status'],'completed_runs':len(acquisition['completed']),
                      'expected_runs':40,'formal_packets':totals,'error':acquisition.get('error')}))


def capture_topology(root):
    from c8_remote import LoggedLab, get_settings
    result={}
    source="""import os,json,pathlib
print(json.dumps({'online_cpus':pathlib.Path('/sys/devices/system/cpu/online').read_text().strip(),
 'cpu_count':os.cpu_count(),'allowed':sorted(os.sched_getaffinity(0)),
 'sched_rt_runtime_us':pathlib.Path('/proc/sys/kernel/sched_rt_runtime_us').read_text().strip()}))
"""
    for port in [2223,2225,2226]:
        host=LoggedLab(get_settings(),port,root/'setup')
        try:result[str(port)]=json.loads(host.run(['python3','-c',source],sudo=True))
        finally:host.client.close()
    dump(root/'setup/cpu-topology.json',result)


def recover_stale_terminals(root,record):
    """Bounded post-campaign recovery; never change subscriber/charging policy."""
    from c8_remote import LoggedLab,get_settings
    from c8_xdp_window import CLI
    assert json.loads((root/'setup/acquisition.json').read_text())['status']=='completed'
    assert json.loads((root/'setup/xdp-window.json').read_text())['restored']
    units={'001':'ueransim-ue','002':'maestro-ue-vehicle','003':'maestro-ue-sensor',
           '004':'ueransim-ue-04','005':'ueransim-ue-05','006':'ueransim-ue-06'}
    missing=[]
    for row in record['terminals']:
        nf={'urllc':'upf3','miot':'upf2','embb':'upf'}[row['slice']]
        if row['address'] not in {s['ue_ipv4'] for s in record['native'][nf]['native']['sessions']}:
            missing.append(row)
    assert missing, 'recovery_requires_observed_stale_NAS_session'
    path=root/'setup/oe4/stale-session-recovery.json'
    assert not path.exists(), 'bounded_recovery_already_attempted'
    recovery={'at':datetime.now(timezone.utc).isoformat(),'stale_sessions':missing,
              'charging_or_subscriber_policy_modified':False,'status':'started'}
    dump(path,recovery)
    host=LoggedLab(get_settings(),2226,root/'setup/oe4')
    try:
        for row in missing:
            host.run([CLI,row['supi'],'-e','ps-release-all'],sudo=True,check=False)
            host.run(['systemctl','restart',units[row['supi'][-3:]]],sudo=True)
        time.sleep(8)
        recovery['status']='restart_completed'
    finally:
        dump(path,recovery);host.client.close()


def scheduler_identity(record):
    assert record['success'], 'scheduler_verification_failed'
    identities=[]
    expected={'2226':{'maestro-ue-vehicle','ueransim-ue-05'},
              '2225':{'ueransim-gnb'},'2223':{'open5gs-upfd-urllc','maestro-terminal-echo-mec'}}
    assert set(record['hosts'])==set(expected)
    for port,units in record['hosts'].items():
        assert {u['after']['unit'] for u in units}==expected[port]
        for unit in units:
            after=unit['after']
            assert after['pid']==unit['before']['pid'] and after['threads']
            for thread in after['threads']:
                assert thread['policy']==2 and thread['priority']==10 and thread['affinity'], 'not_SCHED_RR_10'
                identities.append((port,after['unit'],after['pid'],thread['tid'],tuple(thread['affinity'])))
    return tuple(sorted(identities))


def validate_rt_evidence(root,protocol,campaigns):
    prior=json.loads((root/'setup/previous-n20-sha256.json').read_text())
    previous=root.parent/prior['directory']
    actual={p.relative_to(previous).as_posix() for p in previous.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    assert actual==set(prior['files']), 'previous_N20_file_set_changed'
    assert all(hashlib.sha256((previous/name).read_bytes()).hexdigest()==digest for name,digest in prior['files'].items()), 'previous_N20_bytes_changed'
    scheduler={}
    hashes={}
    for path in sorted((root/'setup/scheduler').glob('*.json')):
        record=json.loads(path.read_text())
        assert record['label'] not in scheduler
        scheduler[record['label']]=record
        hashes[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    expected={f'b{b}-{m}-{edge}' for b in range(20) for m in ['kernel','xdp'] for edge in ['before','after']}
    assert expected <= set(scheduler), 'missing_scheduler_window'
    reference=scheduler_identity(scheduler['after-prepare'])
    assert all(scheduler_identity(scheduler[label])==reference for label in expected), 'critical_thread_identity_or_affinity_changed'
    scheduler_identity(scheduler['after-restore'])
    window=json.loads((root/'setup/xdp-window.json').read_text())
    assert window['charging_guard']=={'charging_enforcement':False,'nchf':False}
    deltas=[]
    totals={m:{'UL_OK':0,'DL_OK':0} for m in ['kernel','xdp']}
    for campaign in campaigns:
        for trial in campaign['trials']:
            if trial['config']['warmup']:continue
            prefix=root/'runs'/campaign['run_id']/trial['name']
            before=json.loads(Path(str(prefix)+'-before.json').read_text())['bpf_counters']
            after=json.loads(Path(str(prefix)+'-after.json').read_text())['bpf_counters']
            delta={k:after[k]-before[k] for k in ['UL_OK','DL_OK']}
            assert all(v>=0 for v in delta.values()), 'BPF_counter_reset'
            mode=campaign['arguments']['mode']
            if mode=='kernel':assert delta=={'UL_OK':0,'DL_OK':0}, 'XDP_active_in_kernel_arm'
            for k,v in delta.items():totals[mode][k]+=v
            deltas.append({'run_id':campaign['run_id'],'block':trial['config']['block'],'mode':mode,**delta})
    dump(root/'evidence/bpf-per-run.json',{'formal_only':deltas,'totals':totals})
    return {'policy':'SCHED_RR','priority':10,'verified_windows':len(expected),
            'previous_n20_files_unchanged':len(prior['files']),
            'critical_thread_identity_and_affinity_stable':True,'charging_guard_preserved':True,
            'formal_bpf_totals':totals,'scheduler_sha256':hashes,
            'execution_mode':window['execution_mode'],'hardware_cycles_measured':False}


def empirical_criteria(stats,certificate):
    metrics=stats['extended']['metrics']
    p50=metrics['rtt_p50_ms'];p95=metrics['rtt_p95_ms']
    pooled=stats['pooled_descriptive_only']
    zero_loss=sum(r['ack'] for r in pooled)==sum(r['attempted'] for r in pooled)==60000
    formal=certificate['realtime_evidence']['formal_bpf_totals']['xdp']
    return {
        'all_60000_packets_delivered':zero_loss,
        'generic_xdp_formal_forwarding_ge_99_8_pct':all(.998<=formal[k]/30000<=1 for k in ['UL_OK','DL_OK']),
        'p50_bca95_strictly_negative':p50['bca']['95'] is not None and p50['bca']['95'][1]<0,
        'p50_both_one_sided_holm_p_lt_005':all(p50[f]['p_less_holm_primary']<.05 for f in ['t','wilcoxon']),
        'p95_mean_paired_reduction':p95['difference']<0,
        'p95_bca95_strictly_negative':p95['bca']['95'] is not None and p95['bca']['95'][1]<0,
        'p99_mean_paired_reduction':metrics['rtt_p99_ms']['difference']<0,
    }


def finalize(root):
    certificate=json.loads((root/'evidence/certification-status.json').read_text())
    assert certificate['accepted'] and certificate['formal_trials']=={'urllc':40}
    recovered=json.loads((root/'setup/oe4/runtime-final.json').read_text())
    assert recovered['status']=='verified' and len(recovered['terminals'])==6
    stats=json.loads((root/'evidence/statistics_ieee.json').read_text())
    criteria=empirical_criteria(stats,certificate)
    result={'created_at':datetime.now(timezone.utc).isoformat(),'acquisition_accepted':True,
            'empirical_criteria':criteria,'all_empirical_criteria_met':all(criteria.values()),
            'original_strict_joint_criterion_met':stats['extended']['primary_joint_criterion_met'],
            'driver_or_hardware_bypass_demonstrated':False,'hardware_cycles_measured':False,
            'absolute_green':False,
            'scope':'Generic XDP on veth; CPU time per URR packet. No native-driver/offload or hardware-cycle claim.',
            'campaign_directory':root.name,'previous_n20_retained':'ieee',
            'no_pooling_or_significance_based_repetition':True}
    result['restored_terminal_sessions']=6
    result['post_restore_canary_packets']=sum(s['received'] for s in recovered['canary_summary']['streams'])
    dump(root/'evidence/oe3-acceptance.json',result)
    dump(BASE/'evidence/oe3-acceptance.json',result)
    # Consolidate current OE4 + MIoT with only this URLLC campaign.
    # The certified v1 archive and all prior raw campaigns remain untouched.
    from c8_thesis_plots import csv_write
    with (BASE/'datasets/trials.csv').open(encoding='utf-8',newline='') as f:
        baseline_rows=[r for r in csv.DictReader(f) if r['experiment']!='urllc']
    with (root/'datasets/ieee_trials.csv').open(encoding='utf-8',newline='') as f:
        current_rows=list(csv.DictReader(f))
    csv_write(BASE/'datasets/trials_v2_consolidated.csv',baseline_rows+current_rows)
    count=0
    with (BASE/'datasets/packets_v2_consolidated.csv').open('w',encoding='utf-8',newline='') as out:
        writer=None
        for source,omit in [(BASE/'datasets/packets.csv',True),(root/'datasets/packets.csv',False)]:
            with source.open(encoding='utf-8',newline='') as f:
                reader=csv.DictReader(f)
                if writer is None:writer=csv.DictWriter(out,fieldnames=reader.fieldnames);writer.writeheader()
                for row in reader:
                    if omit and row['experiment']=='urllc':continue
                    writer.writerow(row);count+=1
    assert count==147720 and len(baseline_rows+current_rows)==100
    index={'created_at':result['created_at'],'accepted':True,'release':'C8 OE3 SCHED_RR N20',
           'formal_trials':{'miot':24,'isolation':12,'urllc':40},'formal_packets':count,
           'formal_trial_count':76,'formal_streams':100,'urllc_directory':root.name,
           'no_pooling_preliminary_and_extended_urllc':True,
           'primary_joint_criterion_met':result['original_strict_joint_criterion_met'],
           'oe3_all_empirical_criteria_met':result['all_empirical_criteria_met'],
           'historical_urllc_directories':['runs/ (N6)','ieee/ (prior N20)']}
    dump(BASE/'evidence/campaign-index.json',index)
    shutil.copyfile(root/'evidence/certification-status.json',BASE/'evidence/certification-status-ieee.json')
    with (root/'datasets/ieee_trials.csv').open(encoding='utf-8',newline='') as f:
        performance_rows=list(csv.DictReader(f))
    performance={}
    for mode in ['kernel','xdp']:
        relevant=[r for r in performance_rows if r['mode']==mode]
        performance[mode]={k:sum(float(r[k]) for r in relevant)/len(relevant)
                           for k in ['receiver_pps','receiver_mbps','process_cpu_us_per_urr_packet','vm_cpu_us_per_urr_packet']}
    dump(root/'evidence/throughput-cpu.json',{'units':{'receiver_pps':'packets/s','receiver_mbps':'payload Mbit/s',
         'process_cpu_us_per_urr_packet':'microseconds/URR packet','vm_cpu_us_per_urr_packet':'microseconds/URR packet'},
         'means_over_20_runs_per_arm':performance,'offered_pps':100,'maximum_capacity_measured':False})
    lines=['','## Cierre OE3 con SCHED_RR: dictamen trazable','',
           '**Adquisición completa y validada; VERDE ABSOLUTO no acreditado.**','',
           '| Criterio empírico | Resultado |','|---|---|']
    lines += [f"| {k} | {'Cumplido' if v else 'No cumplido'} |" for k,v in criteria.items()]
    lines += ['','| Brazo | Goodput [pps] | Payload [Mbit/s] | CPU UPF [µs/paquete URR] | CPU VM [µs/paquete URR] |',
              '|---|---:|---:|---:|---:|']
    for mode,values in performance.items():
        lines.append('| '+mode+' | '+' | '.join(f'{v:.6f}' for v in values.values())+' |')
    lines += ['','Promedios de 20 corridas por brazo. La carga ofrecida es 100 paquetes/s de 64 bytes; '
              'este goodput no mide la capacidad máxima del UPF. CPU VM incluye otros servicios y observación. '
              'UPF, gNB y UE expusieron una sola vCPU online (CPU 0); no existían núcleos invitados adicionales para pinning.']
    totals=certificate['realtime_evidence']['formal_bpf_totals']['xdp']
    lines += ['',f"Contadores de las ventanas formales XDP: UL_OK={totals['UL_OK']}, DL_OK={totals['DL_OK']} de 30000 paquetes por dirección. ",
              'El modo verificado es XDP genérico sobre veth. No se acredita bypass en driver ni offload de hardware. '
              'CPU/paquete expresa microsegundos, no ciclos PMU. '
              'Se conservaron las 40 corridas, 80 verificaciones temporales, las PDU/QER y los 785 archivos congelados. '
              'El servicio de referencia fue restaurado y SCHED_RR 10 reaplicado al finalizar.','',
              'Comprobación posterior separada de la muestra formal: seis sesiones/rutas nativas verificadas '
              'y 100/100 respuestas MEC por cada vehículo. Estos 200 paquetes de recuperación no se incorporan a los contrastes.','',
              'Reproducción del análisis desde la raíz del repositorio (Python del entorno backend):','',
              '```powershell',
              f'python infra/c8_report_ieee.py --root .work/c8-campaign/{root.name}',
              f'python infra/c8_ieee_plots.py --root .work/c8-campaign/{root.name}',
              f'python infra/c8_thesis_plots.py --root .work/c8-campaign/{root.name} --ieee',
              f'python infra/c8_validate.py --root .work/c8-campaign/{root.name} --profile ieee',
              f'python infra/c8_oe3.py --root .work/c8-campaign/{root.name}',
              'python infra/c8_package.py --zip','python infra/c8_package.py --verify','```','',
              'La adquisición fija se realizó con `python infra/c8_run_xdp_ieee.py --preregister` '
              'y `python infra/c8_run_xdp_ieee.py`; el ejecutor rechaza reiniciar sobre un lote existente.','']
    report=(root/'evidence/chapter4_results_ieee.md').read_text(encoding='utf-8').split('\n## Cierre OE3 con SCHED_RR:',1)[0].rstrip()+'\n'+'\n'.join(lines)
    for path in [root/'evidence/chapter4_results_ieee.md',BASE/'evidence/chapter4_results_ieee.md',ROOT/'docs/C8_OE3_URLLC_RR.md']:
        path.write_text(report,encoding='utf-8')
    plan=ROOT/'docs/PLAN_CIERRE_TESIS_MAESTRO_5G.md'
    text=plan.read_text(encoding='utf-8')
    marker='<!-- OE3_RR_STATUS -->'
    if marker in text:
        start=text.index(marker);end=text.index('<!-- /OE3_RR_STATUS -->',start)+len('<!-- /OE3_RR_STATUS -->')
        text=text[:start]+text[end:]
    summary=(marker+'\n**OE3, campaña SCHED_RR N=20: adquisición completa y validada; '
             'VERDE ABSOLUTO no acreditado.** 40 corridas de 15 s; '
             +('60000/60000 paquetes entregados. ' if criteria['all_60000_packets_delivered'] else 'Consultar pérdidas observadas en el acta. ')
             +'Criterio empírico conjunto: '+('cumplido' if result['all_empirical_criteria_met'] else 'no cumplido')
             +'. XDP genérico sobre veth; sin demostración de bypass de driver ni ciclos PMU. '
             '[Tabla completa, significancia, contadores y límites](C8_OE3_URLLC_RR.md). '
             'Los lotes anteriores permanecen íntegros y separados.\n<!-- /OE3_RR_STATUS -->\n\n')
    first,rest=text.split('\n',1);plan.write_text(first+'\n\n'+summary+rest.lstrip(),encoding='utf-8')
    print(json.dumps(result))


def release(root):
    """Verify recovery once, then reproduce the complete offline release."""
    acquisition=json.loads((root/'setup/acquisition.json').read_text())
    assert acquisition['status']=='completed' and len(acquisition['completed'])==40
    recovered=root/'setup/oe4/runtime-final.json'
    if not (root/'setup/cpu-topology.json').exists():capture_topology(root)
    needs_verification=not recovered.exists()
    if recovered.exists() and json.loads(recovered.read_text())['status']!='verified':
        old=json.loads(recovered.read_text())
        preserved=recovered.with_name('runtime-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        shutil.copyfile(recovered,preserved)
        recover_stale_terminals(root,old)
        needs_verification=True
    if needs_verification:
        # UE restarts change RLS UDP ports. Rebind the pre-existing OE4 QoS
        # only after all formal trials and XDP teardown, preserving both arms.
        subprocess.run([sys.executable,str(ROOT/'infra/c8_qos.py'),'apply'],cwd=ROOT,
                       env={**os.environ,'C8_CAMPAIGN_ROOT':str(root)},check=True)
        subprocess.run([sys.executable,str(ROOT/'infra/c8_oe4_runtime.py')],cwd=ROOT,
                       env={**os.environ,'C8_CAMPAIGN_ROOT':str(root)},check=True)
    assert json.loads(recovered.read_text())['status']=='verified'
    commands=[('c8_report_ieee.py',['--root',str(root)]),
              ('c8_ieee_plots.py',['--root',str(root)]),
              ('c8_validate.py',['--root',str(root),'--profile','ieee']),
              ('c8_oe3.py',['--root',str(root)]),
              ('c8_package.py',['--zip']),('c8_package.py',['--verify'])]
    for script,args in commands:
        subprocess.run([sys.executable,str(ROOT/'infra'/script),*args],cwd=ROOT,check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=BASE/'ieee-rr')
    parser.add_argument('--seal-previous',action='store_true')
    parser.add_argument('--progress',action='store_true')
    parser.add_argument('--release',action='store_true')
    args=parser.parse_args()
    (release if args.release else progress if args.progress else seal_previous if args.seal_previous else finalize)(args.root.resolve())
