"""Validate, analyze and seal only the isolated 2-vCPU campaign, offline."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import numpy as np
from c8_2vcpu_stats import analyze_rows
from c8_ieee_stats import load_trials, PRIMARY
from c8_thesis_plots import csv_write, summarize, style, urllc_figures
from c8_ieee_plots import extended_figures
from c8_report_ieee import LABELS
from c8_oe3 import scheduler_identity

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'.work/c8-campaign/ieee-2vcpu'


def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))


def dump(p,data):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def validate(root):
    protocol=read(root/'setup/preregistered-design.json')
    assert digest(root/'setup/preregistered-design.json')==(root/'setup/preregistered-design.sha256').read_text().strip()
    acquisition=read(root/'setup/acquisition.json')
    assert acquisition['status']=='completed' and len(acquisition['completed'])==40
    assert protocol['created_at']<acquisition['started_at']
    campaigns=[(p,read(p)) for p in sorted((root/'runs').glob('*/campaign.json'))]
    assert len(campaigns)==40 and all(c['status']=='completed' and not c['pilot'] for _,c in campaigns)
    assert not read(root/'setup/exclusions.json')['runs']
    assert [(c['arguments']['block_start'],c['arguments']['mode']) for _,c in campaigns]==[(b,m) for b,modes in enumerate(protocol['order']) for m in modes]
    assert Counter(tuple(x) for x in protocol['order'])=={('kernel','xdp'):10,('xdp','kernel'):10}
    pilot=read(root/'pilot/assessment.json')
    assert pilot['passed'] and all(pilot['checks'].values())
    for entry in pilot['runs']:
        folder=root/'pilot/runs'/entry['run_id']
        raw=read(next(folder.glob('001-*-raw.json')))
        packets=raw['streams'][0]['packets']
        assert len(packets)==1500 and all(p['ack'] and p['send_ok'] for p in packets)
        if entry['mode']=='xdp':
            assert np.quantile([p['rtt_ms'] for p in packets],.95)<5
            assert np.quantile([p['rtt_ms'] for p in packets],.99)<10
    identities=set();qers=[];sources=[];raws=[];rows=[];bpf=[];scheduling=[]
    native_all={nf:[] for nf in ['upf','upf2','upf3']}
    for path,c in campaigns:
        sources.append(c['source_sha256'])
        for name,h in c['source_sha256'].items():assert digest(path.parent/'source'/name)==h
        trial,=c['trials'];name=trial['name'];config=trial['config']
        raw=read(path.parent/(name+'-raw.json'));before=read(path.parent/(name+'-before.json'));after=read(path.parent/(name+'-after.json'))
        assert raw['configuration']==config and not config['warmup']
        assert (config['duration_s'],config['seed'])==(15,42017+config['block'])
        stream,=raw['streams'];packets=stream['packets'];spec=stream['spec']
        assert len(packets)==1500 and [p['sequence'] for p in packets]==list(range(1500))
        assert (spec['pps'],spec['payload_bytes'],spec['target'],spec['port'])==(100,64,'172.31.48.2',8765)
        assert not stream['duplicates'] and not stream['foreign_count']
        for p in packets:
            if p['ack']:assert p['send_ok'] and p['sensor_match'] and p['rtt_ms']==(p['received_ns']-p['sent_ns'])/1e6 and p['rtt_ms']>=0
        warmup=read(path.parent/'excluded-warmup-raw.json')
        assert warmup['spec']==spec and warmup['duration_s']==5 and len(warmup['packets'])==500
        assert all(p['ack'] and p['send_ok'] for p in warmup['packets'])
        ready=read(path.parent/'traffic-ready.json');same=raw['same_socket_warmup']
        assert ready['pid']==same['pid'] and ready['socket']==same['socket'] and same['seconds']==5
        assert max(p['sent_ns'] for p in warmup['packets'])<min(p['sent_ns'] for p in packets)
        for threads in [ready['scheduling'],raw['scheduling_before'],raw['scheduling_after'],*stream['receiver_scheduling'].values()]:
            assert all(t['policy']==2 and t['priority']==10 and t['affinity']==[1] for t in threads)
        for snap in [before,after]:
            for nf in native_all:
                n=snap[nf]['native'];sessions=sorted(n['sessions'],key=lambda s:s['ue_ipv4'])
                assert len(sessions)==2
                native_all[nf].append({'pid':n['pid'],'generation':n['generation'],
                    'sessions':[{k:s[k] for k in ['ue_ipv4','upf_seid','smf_seid','rules']} for s in sessions]})
            native=snap['upf3']['native'];ss=sorted(native['sessions'],key=lambda s:s['ue_ipv4']);assert len(ss)==2
            identities.add((native['pid'],native['generation'],tuple((s['ue_ipv4'],s['upf_seid'],s['smf_seid']) for s in ss)))
            qers.append([s['rules']['qer'] for s in ss])
        delta={k:after['bpf_counters'][k]-before['bpf_counters'][k] for k in ['UL_OK','DL_OK']}
        assert all(0<=v<=1500 for v in delta.values())
        if config['mode']=='kernel':assert delta=={'UL_OK':0,'DL_OK':0}
        else:assert all(v>=1497 for v in delta.values())
        bpf.append({'run_id':c['run_id'],'block':config['block'],'mode':config['mode'],**delta})
        row,=summarize(raw,before,after)[0]
        assert row['identity_stable'] and row['qer_unchanged'] and row['urr_matches_endpoint']
        rows.append(row);raws.append(raw)
        for edge in ['before','after']:
            label=f"b{config['block']}-{config['mode']}-{edge}"
            matches=list((root/'setup/scheduler').glob(label+'-*.json'));assert len(matches)==1
            sched=read(matches[0]);scheduling.append(scheduler_identity(sched))
            assert all(t['affinity']==[1] for units in sched['hosts'].values() for unit in units for t in unit['after']['threads'])
            assert all(t['cpu_count']==2 and t['allowed']==[0,1] for t in sched['topology'].values())
            host_label=f"b{config['block']}-{config['mode']}-host-{edge}"
            matches=list((root/'setup').glob(host_label+'-*.json'));assert len(matches)==1
            host=read(matches[0]);assert host['verified'] and host['expected_priority']=='Normal' and len(host['processes'])==5
            assert all(p['priority']=='Normal' and p['role']=='hardened-executor' and p['affinity']==p['expected_affinity'] for p in host['processes'])
    assert len(identities)==1 and all(q==qers[0] for q in qers)
    assert all(all(s==states[0] for s in states) for states in native_all.values())
    assert all(s==sources[0] for s in sources) and all(s==scheduling[0] for s in scheduling)
    window=read(root/'setup/xdp-window.json');assert window['restored'] and window['mode']=='restore'
    assert window['charging_guard']=={'charging_enforcement':False,'nchf':False}
    final=read(root/'setup/oe4/runtime-final.json');assert final['status']=='verified' and len(final['terminals'])==6
    assert all(s['sent']==s['received']==100 for s in final['canary_summary']['streams'])
    irq=read(root/'setup/guest-irq-isolation.json');assert irq['status']=='restored'
    assert all(h['network_irq_cpu0_verified'] and h['restoration']['restored'] for h in irq['hosts'].values())
    frozen=read(root.parent/'setup/frozen-source-start.json');assert len(frozen)==785
    assert all(digest(ROOT/n)==h for n,h in frozen.items())
    preserved=read(root/'setup/preserved-original-pilot.json')
    assert all(digest(root/'history/original-pilot'/n)==h for n,h in preserved.items())
    archives=read(root/'setup/preserved-archives.json')
    assert all(digest(Path(n))==h for n,h in archives.items())
    optimized=read(root/'setup/preserved-optimized-sleep-pilot.json')
    assert all(digest(root/'history/optimized-sleep-pilot'/n)==h for n,h in optimized.items())
    shared_host=read(root/'setup/preserved-timerfd-shared-host-pilot.json')
    assert all(digest(root/'history/timerfd-shared-host-pilot'/n)==h for n,h in shared_host.items())
    apps=read(root/'setup/host-application-affinity.json')
    assert apps['status']=='restored' and all(p['restored'] in [True,'process_exited'] for p in apps['processes'])
    for dirname in ['ieee','ieee-rr']:
        prior=read(root/'setup'/('preserved-'+dirname+'.json'))
        assert all(digest(root.parent/dirname/n)==h for n,h in prior.items())
        assert set(prior)=={p.relative_to(root.parent/dirname).as_posix() for p in (root.parent/dirname).rglob('*') if p.is_file()}
    totals={m:{k:sum(r[k] for r in bpf if r['mode']==m) for k in ['UL_OK','DL_OK']} for m in ['kernel','xdp']}
    ack=sum(r['received'] for r in rows)
    certificate={'validated':True,'formal_runs':40,'formal_packets':60000,'formal_ack':ack,'warmup_excluded_packets':20000,
                 'all_packets_delivered':ack==60000,'formal_bpf_totals':totals,
                 'formal_xdp_share_pct':{k:totals['xdp'][k]/30000*100 for k in totals['xdp']},
                 'fast_share_ge_99_8_pct':all(v>=29940 for v in totals['xdp'].values()),
                 'scheduler_windows_verified':80,'host_windows_verified':80,'qer_and_pdu_unchanged':True,
                 'frozen_files_unchanged':785,'prior_campaigns_unchanged':True,'restored_six_sessions':True,
                 'host_priority':'Normal, explicitly authorized deviation','hypervisor_backend':'Hyper-V/NEM',
                 'pilot_latency_gate_passed':True,'inference_unit':'20 paired blocks, not packets',
                 'six_sessions_stable_all_windows':True,'historic_pilot_files_unchanged':len(preserved),
                 'previous_archives_unchanged':len(archives),
                 'personal_application_affinity_restored':True,
                 'guest_governors':{p:r['status'] for p,r in read(root/'setup/governor/assessment.json').items()},
                 'scope':'Generic XDP/veth; process affinity is not an exclusive physical CPU reservation; no hardware cycle measurement'}
    dump(root/'evidence/bpf-per-run.json',{'runs':bpf,'totals':totals})
    dump(root/'evidence/certification-status.json',certificate)
    return certificate,raws


def build(root):
    certificate,raws=validate(root)
    rows=load_trials(root);analysis=analyze_rows(rows)
    protocol=read(root/'setup/preregistered-design.json')
    for folder in ['datasets','figures','evidence']:(root/folder).mkdir(exist_ok=True)
    pooled=[]
    for mode in ['kernel','xdp']:
        ps=[p for raw in raws if raw['configuration']['mode']==mode for p in raw['streams'][0]['packets']]
        values=[p['rtt_ms'] for p in ps if p['ack']]
        pooled.append({'mode':mode,'attempted':len(ps),'ack':len(values),'mean_ms':float(np.mean(values)),
                       **{f'p{q}_ms':float(np.quantile(values,q/100)) for q in [50,95,99]},'max_ms':max(values),'loss_pct':100*(1-len(values)/len(ps)),
                       'ack_lt10ms_pct':100*sum(p['ack'] and p['rtt_ms']<10 for p in ps)/len(ps),
                       'sender_lateness_p99_ms':float(np.quantile([(p['sent_ns']-p['scheduled_ns'])/1e6 for p in ps],.99))})
    xdp=next(r for r in pooled if r['mode']=='xdp')
    certificate['formal_pooled_latency_criterion_met']=xdp['p95_ms']<5 and xdp['p99_ms']<10
    certificate['xdp_runs_meeting_latency_criteria']=sum(r['mode']=='xdp' and r['rtt_p95_ms']<5 and r['rtt_p99_ms']<10 for r in rows)
    certificate['primary_joint_criterion_met']=analysis['primary_joint_criterion_met']
    certificate['amended_protocol_met']=all(certificate[k] for k in ['all_packets_delivered','fast_share_ge_99_8_pct','pilot_latency_gate_passed','primary_joint_criterion_met'])
    certificate['verdict']='VERDE EXPERIMENTAL' if certificate['amended_protocol_met'] and certificate['formal_pooled_latency_criterion_met'] else 'CRITERIOS NO CUMPLIDOS'
    certificate['normative_3gpp_certification']=False
    report={'created_at':datetime.now(timezone.utc).isoformat(),'extended':analysis,'pooled_descriptive_only':pooled,
            'preregistration_sha256':digest(root/'setup/preregistered-design.json'),
            'order_sensitivity':{m:{label:float(np.mean([d for d,o in zip(analysis['metrics'][m]['differences'],protocol['order']) if o[0]==first]))
                                   for label,first in [('AB','kernel'),('BA','xdp')]} for m in PRIMARY}}
    dump(root/'evidence/statistics_ieee.json',report)
    csv_write(root/'datasets/ieee_trials.csv',rows);csv_write(root/'datasets/ieee_pooled_rtt.csv',pooled)
    flat=[]
    for metric,r in analysis['metrics'].items():
        flat.append({'metric':metric,**{k:r[k] for k in ['n_pairs','kernel_mean','kernel_sd','xdp_mean','xdp_sd','difference','difference_sd','cohen_dz']},
                    **{f'bca{level}_{edge}':r['bca'][level][i] if r['bca'][level] else None for level in ['95','99'] for i,edge in enumerate(['low','high'])},
                    **{f'{family}_{key}':r[family].get(key) for family in ['t','wilcoxon'] for key in ['p_less','p_two_sided','p_less_holm_primary']}})
    csv_write(root/'datasets/chapter4_statistics.csv',flat)
    packets=[{'run_id':raw['configuration']['run_id'],'mode':raw['configuration']['mode'],'block':raw['configuration']['block'],**p} for raw in raws for p in raw['streams'][0]['packets']]
    csv_write(root/'datasets/packets.csv',packets)
    style();urllc_figures(root/'figures',rows,raws);extended_figures(root,rows,raws)
    for stem in ['urllc_rtt_cdf','urllc_cpu_per_packet','urllc_paired_block_differences']:
        for ext in ['png','svg','pdf']:assert (root/'figures'/(stem+'.'+ext)).stat().st_size>1000
    ci=lambda v:'no definido' if v is None else f'[{v[0]:.4f}, {v[1]:.4f}]'
    lines=['# OE3: campaña 2-vCPU N=20','',
           f"Adquisición: 40 corridas, {certificate['formal_ack']}/60000 ACK. Criterio conjunto estadístico: **{'cumplido' if analysis['primary_joint_criterion_met'] else 'no cumplido'}**.",'',
           f"Dictamen: **{certificate['verdict']}**. La enmienda autorizada el 08-10-2026 y sellada antes de esta adquisición sustituye p100 por p95<5 ms y p99<10 ms para el piloto XDP. El piloto anterior permanece íntegro en history/original-pilot. Windows Normal fue autorizado; Hyper-V/NEM permanece activo.",'',
           'Estos umbrales son criterios experimentales de RTT de aplicación. TS 22.261 Rel.16, sección 7.2, establece requisitos por escenario; no define la combinación universal p95/p99/99,8%. ACK y proporción de encaminamiento XDP no equivalen a confiabilidad de entrega dentro de un plazo. No se certifica conformidad 3GPP ni se atribuyen causalmente los picos al emisor. Referencia: https://www.etsi.org/deliver/etsi_TS/122200_122299/122261/16.12.00_60/ts_122261v161200p.pdf','',
           'UPF y UE no exponen cpufreq al invitado: performance no se puede fijar ni verificar. Windows usa el plan Alto rendimiento. El emisor usa timerfd absoluto y un solo hilo para envío/recepción, paquetes preparados y drenaje del mismo socket calentado; se conserva cada intento y el retraso de envío. El primer piloto optimizado con dos hilos falló (p95=5,348 ms; p99=11,219 ms) y se conserva por separado.','',
           'Un segundo piloto con timerfd y aplicaciones del anfitrión sin restricción también falló (p95=8,141 ms; p99=13,986 ms). Después de autorización expresa, Wallpaper Engine, Chrome y Roblox se limitaron temporalmente a E-cores; se restauraron sus afinidades. Los pilotos anteriores y sus fallos no integran la inferencia N=20.','',
           '20 bloques balanceados (10 AB y 10 BA), semilla 42017; 15 s a 100 pps por corrida. Warmup de 5 s/500 paquetes en el mismo socket/proceso, excluido. La pausa de instrumentación entre warmup y ventana formal se conserva en los timestamps.','',
           '## Tabla para el Capítulo 4','',
           'Δ = XDP − Kernel. Media y DE sobre 20 corridas por brazo; la unidad inferencial es el bloque.','',
           '| Métrica | Kernel media ± DE | XDP media ± DE | Δ | Cohen dz | BCa 95% | BCa 99% |',
           '|---|---:|---:|---:|---:|---|---|']
    for metric,r in analysis['metrics'].items():
        dz='no definido' if r['cohen_dz'] is None else f"{r['cohen_dz']:.4f}"
        lines.append(f"| {LABELS[metric]} | {r['kernel_mean']:.4f} ± {r['kernel_sd']:.4f} | {r['xdp_mean']:.4f} ± {r['xdp_sd']:.4f} | {r['difference']:.4f} | {dz} | {ci(r['bca']['95'])} | {ci(r['bca']['99'])} |")
    lines += ['','Distribución agrupada descriptiva (30000 paquetes por brazo; no es la unidad inferencial):','',
              '| Brazo | Media ms | p50 ms | p95 ms | p99 ms | Máximo ms | ACK RTT<10 ms % | Emisor p99 ms |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in pooled:
        lines.append('| '+r['mode']+' | '+' | '.join(f'{r[k]:.4f}' for k in ['mean_ms','p50_ms','p95_ms','p99_ms','max_ms','ack_lt10ms_pct','sender_lateness_p99_ms'])+' |')
    lines += ['',f"Corridas XDP con ambos percentiles dentro del umbral: {certificate['xdp_runs_meeting_latency_criteria']}/20. El máximo y todos los incumplimientos se conservan.",'']
    lines += ['','| Métrica | t unilateral | t bilateral | t Holm | Wilcoxon unilateral | Wilcoxon bilateral | Wilcoxon Holm |','|---|---:|---:|---:|---:|---:|---:|']
    for m,r in analysis['metrics'].items():
        vals=[r[f].get(k) for f in ['t','wilcoxon'] for k in ['p_less','p_two_sided','p_less_holm_primary']]
        lines.append('| '+LABELS[m]+' | '+' | '.join('—' if v is None else f'{v:.6g}' for v in vals)+' |')
    lines += ['','BCa usa 10000 remuestreos comunes de bloques, semilla 42017; IC puntuales al 95% y 99%. t pareado y Wilcoxon exacto mediante distribución de signos con rangos medios y ceros Pratt; se exige simetría para Wilcoxon. Holm se aplica separadamente a los tres endpoints primarios en cada familia. Éxito conjunto: todos los IC95 superiores negativos y ambos p Holm unilaterales <0,01. No se imputan datos ni se eliminan picos.','',
              '## Contabilidad y restauración','',
              f"XDP formal: UL={certificate['formal_bpf_totals']['xdp']['UL_OK']}/30000, DL={certificate['formal_bpf_totals']['xdp']['DL_OK']}/30000. Kernel: ambos contadores cero. 80 verificaciones RR/afinidad y 80 verificaciones del ejecutor Windows. QER y PDU estables entre brazos. Se restauraron Kernel, QoS, IRQ/irqbalance, seis sesiones y canario 200/200.",'',
              'CPU/paquete se expresa en microsegundos de CPU por paquete URR, sin contadores PMU. RTT es tiempo de aplicación; XDP genérico en veth no demuestra offload ni cumplimiento de plazos 3GPP. La carga fija no mide capacidad máxima. Dependencia temporal, co-scheduling de Hyper-V y retrasos del emisor siguen siendo posibles; no se atribuyen causalmente las colas a una única capa.','',
              'Los 785 archivos protegidos y las campañas previas permanecen íntegros. Fuentes ejecutadas, raw, warmup, piloto, enmiendas, fallos de preparación, hashes y restauración quedan en el paquete.','',
              'Reproducir análisis offline: `backend\\.venv\\Scripts\\python.exe infra\\c8_2vcpu_report.py --root .work/c8-campaign/ieee-2vcpu`.','']
    text='\n'.join(lines)
    (root/'evidence/chapter4_results_ieee.md').write_text(text,encoding='utf-8')
    (ROOT/'docs/C8_OE3_URLLC_2VCPU.md').write_text(text,encoding='utf-8')
    certificate['original_protocol_fully_met']=False
    dump(root/'evidence/certification-status.json',certificate)
    return certificate


def seal(root):
    archive=ROOT/'.work/c8-campaign-oe3-2vcpu-reproducibility.zip'
    assert not archive.exists(),'never overwrite sealed evidence'
    source=root/'source';source.mkdir(exist_ok=True)
    for p in (ROOT/'infra').glob('c8*.py'):shutil.copyfile(p,source/p.name)
    shutil.copyfile(ROOT/'infra/c8_2vcpu_pin.ps1',source/'c8_2vcpu_pin.ps1')
    shutil.copyfile(ROOT/'infra/c8_2vcpu_host_noise.ps1',source/'c8_2vcpu_host_noise.ps1')
    tests=source/'tests';tests.mkdir(exist_ok=True)
    for name in ['test_c8_2vcpu_stats.py','test_c8_ieee.py']:shutil.copyfile(ROOT/'infra/tests'/name,tests/name)
    import importlib.metadata
    (root/'requirements-reproduce.txt').write_text('\n'.join(f'{name}=={importlib.metadata.version(name)}' for name in ['numpy','scipy','matplotlib'])+'\n')
    (root/'REPRODUCE.md').write_text('''# Offline reproduction

Extract this ZIP. Python 3.11 is the acquisition analysis interpreter.
Install the numerical versions in requirements-reproduce.txt into a virtual environment.
From the directory containing ieee-2vcpu, run:

    python ieee-2vcpu/source/c8_2vcpu_reproduce.py --root ieee-2vcpu --output reproduced

This verifies all packaged hashes, recomputes 20-pair inference, checks exact numerical
agreement and rebuilds PNG/SVG/PDF figures without SSH or the original repository.
The original report's external 785-file and prior-ZIP audits require the original workspace;
their manifests and recorded results are included. Hashes certify integrity, not outcome.
''',encoding='utf-8')
    files={p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ['inventory.json','inventory.sha256'] and '__pycache__' not in p.parts}
    dump(root/'inventory.json',{'scope':'isolated OE3 2-vCPU campaign; original-protocol deviations explicit','files':files})
    (root/'inventory.sha256').write_text(digest(root/'inventory.json')+'  inventory.json\n')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*files,'inventory.json','inventory.sha256']:z.write(root/name,'ieee-2vcpu/'+name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name,h in files.items():assert hashlib.sha256(z.read('ieee-2vcpu/'+name)).hexdigest()==h
    archive.with_suffix('.zip.sha256').write_text(digest(archive)+'  '+archive.name+'\n')
    print(json.dumps({'zip':str(archive),'zip_sha256':digest(archive),'manifest_sha256':digest(root/'inventory.json'),'files_verified':len(files)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=DEST);p.add_argument('--seal',action='store_true');a=p.parse_args()
    print(json.dumps(build(a.root.resolve())))
    if a.seal:seal(a.root.resolve())
