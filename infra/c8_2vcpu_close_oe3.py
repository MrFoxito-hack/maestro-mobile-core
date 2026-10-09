"""Consolidated OE3 evidence closure; no invented formal trials or causal claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import zipfile
import numpy as np
import matplotlib.pyplot as plt
from c8_ieee_stats import load_trials
from c8_thesis_plots import csv_write, summarize, style, save

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'.work/c8-campaign/ieee-2vcpu'
ARCHIVE=ROOT/'.work/c8-campaign-oe3-2vcpu-reproducibility.zip'
ATTEMPTS=[('original','history/original-pilot'),('absolute_sleep','history/optimized-sleep-pilot'),
          ('timerfd_shared_host','history/timerfd-shared-host-pilot'),('timerfd_ecores','.')]


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def calculate(root):
    rows=[];packets=[];warmups=[]
    for attempt,relative in ATTEMPTS:
        base=root/relative
        paths=sorted((base/'pilot/runs').glob('*/campaign.json'))
        assert len(paths)==2
        for path in paths:
            c=read(path);assert c['status']=='completed' and c['pilot']
            for name,h in c['source_sha256'].items():assert digest(path.parent/'source'/name)==h
            trial,=c['trials'];name=trial['name']
            raw=read(path.parent/(name+'-raw.json'));before=read(path.parent/(name+'-before.json'));after=read(path.parent/(name+'-after.json'))
            row,=summarize(raw,before,after)[0];stream,=raw['streams'];ps=stream['packets']
            assert len(ps)==1500 and [p['sequence'] for p in ps]==list(range(1500))
            assert all(p['send_ok'] and p['ack'] and p['sensor_match'] for p in ps)
            assert not stream['duplicates'] and not stream['foreign_count']
            assert all(p['rtt_ms']==(p['received_ns']-p['sent_ns'])/1e6 for p in ps)
            assert row['identity_stable'] and row['qer_unchanged'] and row['urr_matches_endpoint']
            delta={k:after['bpf_counters'][k]-before['bpf_counters'][k] for k in ['UL_OK','DL_OK']}
            assert set(delta.values())==({1500} if row['mode']=='xdp' else {0})
            row.update(attempt=attempt,max_rtt_ms=max(p['rtt_ms'] for p in ps),
                       sender_lateness_p99_ms=float(np.quantile([(p['sent_ns']-p['scheduled_ns'])/1e6 for p in ps],.99)),
                       six_native_sessions=all(len(before[n]['native']['sessions'])==len(after[n]['native']['sessions'])==2 for n in ['upf','upf2','upf3']),
                       **delta)
            rows.append(row)
            packets.extend({'attempt':attempt,'mode':row['mode'],**p} for p in ps)
            warmup=read(path.parent/'excluded-warmup-raw.json')['packets']
            assert len(warmup)==500 and all(p['ack'] for p in warmup)
            warmups.extend({'attempt':attempt,'mode':row['mode'],**p} for p in warmup)
        assert not list((base/'runs').glob('*/campaign.json'))
        protocol=base/'setup/preregistered-design.json'
        assert digest(protocol)==protocol.with_suffix('.sha256').read_text().strip()
    prior=load_trials(root/'supporting-evidence/ieee-rr-cpu')
    assert len(prior)==40 and all(r['urr_matches_endpoint'] and r['identity_stable'] and r['qer_unchanged'] for r in prior)
    topology=read(root/'supporting-evidence/ieee-rr-cpu/setup/cpu-topology.json')
    assert all(r['cpu_count']==1 for r in topology.values())
    cpu={m:float(np.mean([r['process_cpu_us_per_urr_packet'] for r in prior if r['mode']==m])) for m in ['kernel','xdp']}
    cpu['reduction_pct']=100*(1-cpu['xdp']/cpu['kernel']);cpu['vcpu']=1;cpu['pairs']=20
    wake={}
    for port in [2223,2226]:
        values=read(root/f'setup/idle-wakeup-diagnostic/{port}.json')['lateness_ms'];assert len(values)==500
        wake[str(port)]={'samples':len(values),'p95_ms':float(np.quantile(values,.95)),
                         'p99_ms':float(np.quantile(values,.99)),'max_ms':max(values)}
    return {'pilot_rows':rows,'prior_cpu':cpu,'idle_wakeup':wake},packets,warmups,prior


def audit(root):
    result={}
    frozen=read(root/'supporting-evidence/frozen-source-start.json');assert len(frozen)==785
    assert all(digest(ROOT/n)==h for n,h in frozen.items());result['protected_files_unchanged']=785
    for name,folder in [('original','original-pilot'),('optimized-sleep','optimized-sleep-pilot'),('timerfd-shared-host','timerfd-shared-host-pilot')]:
        manifest=read(root/f'setup/preserved-{name}-pilot.json')
        assert all(digest(root/'history'/folder/n)==h for n,h in manifest.items())
        result[name+'_files_verified']=len(manifest)
    for name in ['ieee','ieee-rr']:
        manifest=read(root/f'setup/preserved-{name}.json');base=root.parent/name
        assert set(manifest)=={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
        assert all(digest(base/n)==h for n,h in manifest.items());result[name+'_unchanged']=True
    archives=read(root/'setup/preserved-archives.json')
    assert all(digest(Path(n))==h for n,h in archives.items());result['prior_archives_unchanged']=len(archives)
    final=read(root/'setup/oe4/runtime-final.json')
    assert final['status']=='verified' and len(final['terminals'])==6
    assert all(r['sent']==r['received']==100 for r in final['canary_summary']['streams'])
    for terminal in final['terminals']:
        nf={'urllc':'upf3','embb':'upf','miot':'upf2'}[terminal['slice']]
        assert sum(s['ue_ipv4']==terminal['address'] for s in final['native'][nf]['native']['sessions'])==1
    window=read(root/'setup/xdp-window.json');assert window['restored'] and window['mode']=='restore'
    irq=read(root/'setup/guest-irq-isolation.json');assert irq['status']=='restored'
    assert all(h['restoration']['restored'] for h in irq['hosts'].values())
    assert read(root/'setup/host-application-affinity.json')['status']=='restored'
    assert all(r['verified'] for r in read(root/'setup/final-application-affinity-check.json'))
    result.update(final_sessions=6,final_canary_ack=200,final_check_at=final['at'],kernel_restored=True,
                  irq_restored=True,application_affinity_restored=True)
    return result


def figures(root,out,summary,packets):
    style();(out/'figures').mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(2,2,figsize=(7,5.6))
    for ax,(attempt,_) in zip(axes.flat,ATTEMPTS):
        for mode,color in [('kernel','#0072B2'),('xdp','#D55E00')]:
            values=np.sort([p['rtt_ms'] for p in packets if p['attempt']==attempt and p['mode']==mode])
            ax.step(values,np.arange(1,len(values)+1)/len(values),where='post',label=mode,color=color)
        ax.axvline(10,color='0.4',linestyle=':',linewidth=.8)
        ax.set(xlabel='Application RTT [ms]',ylabel='Empirical CDF',title=attempt,xlim=(0,None),ylim=(0,1.02));ax.legend(fontsize=7)
    fig.suptitle('Separate pilots; full observed ranges; no pooled inference',fontsize=9)
    save(fig,out/'figures/oe3_all_pilot_cdfs')
    fig,axes=plt.subplots(1,2,figsize=(7,3.1))
    for ax,port,title in zip(axes,[2223,2226],['UPF VM: no generated MEC traffic','UE VM: no generated MEC traffic']):
        values=read(root/f'setup/idle-wakeup-diagnostic/{port}.json')['lateness_ms']
        ax.plot(range(500),values,linewidth=.7,color='#0072B2')
        ax.set(xlabel='Periodic wake-up index',ylabel='Wake-up lateness [ms]',title=title,ylim=(0,None))
    save(fig,out/'figures/oe3_idle_wakeup')
    fig,axes=plt.subplots(1,2,figsize=(7,3.1))
    original={r['mode']:r for r in summary['pilot_rows'] if r['attempt']=='original'}
    for ax,values,title,label in [
        (axes[0],[original[m]['rtt_p50_ms'] for m in ['kernel','xdp']],'Original 2-vCPU pilot: one pair','Application RTT p50 [ms]'),
        (axes[1],[summary['prior_cpu'][m] for m in ['kernel','xdp']],'Prior 1-vCPU campaign: 20 pairs','UPF process CPU [us / URR packet]')]:
        ax.bar(['Kernel','XDP'],values,color=['#0072B2','#D55E00']);ax.set(title=title,ylabel=label,ylim=(0,max(values)*1.22))
        for i,v in enumerate(values):ax.text(i,v,f'{v:.3f}',ha='center',va='bottom',fontsize=8)
    save(fig,out/'figures/oe3_central_rtt_and_prior_cpu')


def report(summary,status):
    rows=summary['pilot_rows'];original={r['mode']:r for r in rows if r['attempt']=='original'};cpu=summary['prior_cpu'];wake=summary['idle_wakeup']
    lines=['# OE3: acta final de cierre técnico y evidencia reproducible','',
        '**Cierre documental autorizado el 08-10-2026. Implementación XDP validada funcionalmente en las muestras observadas; mejora central y eficiencia documentadas con su procedencia. No se acredita cumplimiento URLLC estricto ni VERDE ABSOLUTO. La campaña formal 2-vCPU N=20 no se ejecutó: 0/40 corridas y 0/60000 paquetes formales.**','',
        '## Implementación y resultado central','',
        'En cada uno de los cuatro pilotos 2-vCPU, XDP entregó 1500/1500 paquetes (0% de pérdidas observadas), con UL_OK=1500 y DL_OK=1500: 100% por dirección en el fast-path. Kernel tuvo deltas XDP cero. Se conservaron 12000 intentos de piloto y 4000 paquetes de calentamiento, separados de la muestra formal inexistente. Este resultado valida la función bajo la carga ensayada; no garantiza una tasa de éxito poblacional del 100%. XDP es genérico sobre veth, no offload en hardware.','',
        f"En el piloto original de 2-vCPU, p50 fue {original['kernel']['rtt_p50_ms']:.7f} ms en Kernel y {original['xdp']['rtt_p50_ms']:.7f} ms en XDP: diferencia {original['xdp']['rtt_p50_ms']-original['kernel']['rtt_p50_ms']:.7f} ms ({100*(1-original['xdp']['rtt_p50_ms']/original['kernel']['rtt_p50_ms']):.2f}% de reducción descriptiva). Los valores 2,66 y 1,39 ms son truncamientos; al redondear a dos decimales resultan 2,67 y 1,40 ms.",'',
        '## CPU: procedencia y unidades','',
        f"La reducción de **{cpu['reduction_pct']:.2f}%**, de **{cpu['kernel']:.6f} a {cpu['xdp']:.6f} µs por paquete URR**, procede de la campaña anterior `ieee-rr`, con **1 vCPU y 20 pares**, no del piloto 2-vCPU. Se recalculó desde sus 40 registros raw y snapshots, incluidos en `supporting-evidence/ieee-rr-cpu`. Son medias por brazo de tiempo CPU del proceso UPF; no tiempo RTT, CPU total de VM, ciclos PMU ni capacidad máxima.",'',
        f"En el piloto original 2-vCPU, la métrica CPU correspondiente fue {original['kernel']['process_cpu_us_per_urr_packet']:.3f} frente a {original['xdp']['process_cpu_us_per_urr_packet']:.3f} µs/paquete URR ({100*(1-original['xdp']['process_cpu_us_per_urr_packet']/original['kernel']['process_cpu_us_per_urr_packet']):.2f}% de reducción descriptiva). Es una pareja, sin inferencia N=20. Cada eco produce dos paquetes URR (UL+DL): el denominador es 3000 para 1500 ecos. La fórmula es Δticks / CLK_TCK × 10⁶ / Δpaquetes URR.",'',
        'La campaña previa conservó sus propios resultados y limitaciones: el criterio estadístico conjunto no se cumplió y sus contadores XDP fueron 29939/30000 por dirección, ligeramente por debajo de 99,8%. No se transfieren sus resultados al diseño 2-vCPU.','',
        '## Todos los pilotos 2-vCPU, sin selección de extremos','',
        '| Configuración | Brazo | ACK | p50 ms | p95 ms | p99 ms | Máximo ms | CPU UPF µs/URR |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['attempt']} | {r['mode']} | {r['received']}/1500 | {r['rtt_p50_ms']:.4f} | {r['rtt_p95_ms']:.4f} | {r['rtt_p99_ms']:.4f} | {r['max_rtt_ms']:.4f} | {r['process_cpu_us_per_urr_packet']:.3f} |")
    lines += ['',
        'El piloto original falló la compuerta original p100<10 ms. La enmienda posterior sustituyó esa compuerta por p95<5 ms y p99<10 ms en XDP, preservando el prerregistro previo. Los tres nuevos pilotos fallaron los percentiles. Se conservaron sus configuraciones, fuentes, hashes, calentamiento y paquetes, sin reetiquetarlos como 20 bloques homogéneos. No se calcularon t, Wilcoxon o BCa de N=20 para esta muestra. Las 14 pruebas de las implementaciones estadísticas pasaron; ello valida código, no una hipótesis sobre datos no adquiridos.','',
        '## Diagnóstico de cola y virtualización','',
        f"La prueba de activación periódica sin generar tráfico MEC midió 500 plazos de 10 ms por VM, con CLOCK_MONOTONIC, SCHED_RR 10 y afinidad vCPU1. En UPF: p99={wake['2223']['p99_ms']:.6f} ms y máximo={wake['2223']['max_ms']:.6f} ms. En UE: p99={wake['2226']['p99_ms']:.6f} ms y máximo={wake['2226']['max_ms']:.6f} ms. Los 59–109 ms describen aproximadamente el p99 y el máximo del UPF; no son un límite superior garantizado ni el rango de todos los retardos.",'',
        'Los snapshots `supporting-evidence/EMS-UPF-01-VBox.log` y `EMS-UE-01-VBox.log` registran NEM/Hyper-V y la línea `NEMR3Init: Snail execution mode is active!`; el sistema anfitrión se identifica en `setup/final-host-os.json`. Los servicios 5G permanecían activos durante el diagnóstico, aunque no se generó la carga MEC experimental.','',
        '**Conclusión causal acotada:** existen retrasos de activación fuera del intercambio de paquetes MEC, compatibles con interferencia de planificación/virtualización del entorno Windows–Hyper-V/NEM. La prueba demuestra que no hace falta ese tráfico para observar retrasos; no aísla Hyper-V del scheduler invitado, del emisor o de otros servicios. No demuestra que toda cola RTT sea causada exclusivamente por el hipervisor ni permite excluir toda contribución del plano 5G. Haría falta un contraste controlado con VT-x nativo o bare metal y trazas sincronizadas para esa atribución.','',
        'Se probaron espera absoluta con dos hilos y luego timerfd absoluto con un hilo; ambas versiones preparan paquetes y mantienen el socket calentado. UPF y UE no exponen cpufreq: no pudo verificarse governor performance. El anfitrión usa Alto rendimiento. La afinidad temporal de Wallpaper Engine, Chrome y Roblox a E-cores, expresamente autorizada, tampoco logró superar el piloto y fue restaurada.','',
        'Los umbrales de percentiles son criterios experimentales de RTT de aplicación. [3GPP TS 22.261 Rel.16, sección 7.2](https://www.etsi.org/deliver/etsi_TS/122200_122299/122261/16.12.00_60/ts_122261v161200p.pdf) trata requisitos por escenario; esta evidencia no certifica conformidad 3GPP. El término Snail figura también en el [registro técnico de VirtualBox](https://www.virtualbox.org/ticket/21165), sin que ese nombre sea una medición causal.','',
        '## Restauración e integridad','',
        f"Comprobación final en UTC: {status['final_check_at']}. Seis sesiones NAS/nativas y sus rutas verificadas; canario de 200/200 ACK (100 por vehículo). Kernel, QoS e IRQ/irqbalance restaurados; afinidades de aplicaciones verificadas. La configuración de laboratorio RR 10 y 2-vCPU se conserva. El watchdog temporal de afinidad fue detenido tras la restauración.",'',
        'La primera reanudación detectó sesiones eMBB/MIoT presentes en NAS pero ausentes en sus UPF; se liberaron y restablecieron antes de los pilotos posteriores. Por ello no se afirma continuidad nativa de las seis sesiones durante todos los intentos. También se conservan canarios fallidos y la recuperación posterior.','',
        f"Auditados sin cambios: {status['protected_files_unchanged']} archivos protegidos C0–C7, campañas `ieee/` y `ieee-rr`, {status['prior_archives_unchanged']} ZIP anteriores y los tres historiales de pilotos. La ausencia de corridas formales se verifica directamente.",'',
        '## Uso en el Capítulo 4 y sello','',
        'Redacción propuesta: «OE3 alcanzó la implementación y validación funcional del fast-path eBPF/XDP. El piloto 2-vCPU mostró una reducción descriptiva de la mediana RTT de 2,6656 a 1,3974 ms, con entrega íntegra en la muestra. La campaña previa de 1-vCPU mostró una reducción descriptiva de CPU del proceso UPF del 58,90%. La variabilidad temporal observada incluso sin tráfico MEC limita la evaluación de latencia extrema en el entorno virtualizado. No se acreditó el cumplimiento de las compuertas de cola ni se ejecutó la campaña formal N=20 de 2-vCPU».','',
        'El archivo `c8-campaign-oe3-2vcpu-reproducibility.zip` sella evidencia funcional, resultados descriptivos, intentos fallidos, diagnóstico y restauración. El manifiesto y SHA-256 acreditan integridad y trazabilidad; no equivalen a certificación IEEE o éxito URLLC. El hash externo está en el archivo `.zip.sha256`.','',
        'Reproducir desde el paquete extraído: `python ieee-2vcpu/source/c8_2vcpu_close_oe3.py --root ieee-2vcpu --reproduce reproduced`. Verifica todos los hashes y recalcula tablas/figuras sin SSH. Versiones numéricas en `requirements-reproduce.txt`.','']
    return '\n'.join(lines)


def build(root):
    assert not ARCHIVE.exists(), 'sealed archive already exists; use --verify-zip'
    summary,packets,warmups,prior=calculate(root);status=audit(root)
    status.update(disposition='technical_documentary_closure_authorized',functional_xdp_verified=True,
                  formal_runs=0,formal_packets=0,strict_urllc_criteria_met=False,absolute_green=False,
                  pilot_runs=8,pilot_packets=12000,pilot_ack=12000,warmup_packets=4000,
                  hypervisor_exclusive_causation_established=False,cpu_58_9_percent_source='prior 1-vCPU ieee-rr N=20')
    (root/'datasets').mkdir(exist_ok=True)
    dump(root/'evidence/consolidated-summary.json',summary);dump(root/'evidence/closure-status.json',status)
    csv_write(root/'datasets/all-pilots.csv',summary['pilot_rows']);csv_write(root/'datasets/all-pilot-packets.csv',packets)
    csv_write(root/'datasets/excluded-warmup-packets.csv',warmups);csv_write(root/'datasets/prior-1vcpu-cpu-runs.csv',prior)
    figures(root,root,summary,packets)
    text=report(summary,status)
    (root/'evidence/chapter4_closure.md').write_text(text,encoding='utf-8')
    (ROOT/'docs/C8_OE3_URLLC_2VCPU.md').write_text(text,encoding='utf-8')
    source=root/'source';source.mkdir(exist_ok=True)
    for path in (ROOT/'infra').glob('c8*.py'):shutil.copyfile(path,source/path.name)
    for path in (ROOT/'infra').glob('c8_2vcpu*.ps1'):shutil.copyfile(path,source/path.name)
    (root/'requirements-reproduce.txt').write_text('\n'.join(f'{n}=={importlib.metadata.version(n)}' for n in ['numpy','scipy','matplotlib'])+'\n')
    (root/'REPRODUCE.md').write_text('Python 3.11; install requirements-reproduce.txt.\n\npython ieee-2vcpu/source/c8_2vcpu_close_oe3.py --root ieee-2vcpu --reproduce reproduced\n\nThe script verifies the sealed inventory and independently recalculates all pilot and prior CPU summaries, and the figures. No SSH or formal N=20 inference is performed. The external 785-file and prior archive audit requires the original workspace; its manifests and closure results are retained.\n',encoding='utf-8')
    return status


def seal(root):
    files={p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*')) if p.is_file() and p not in [root/'inventory.json',root/'inventory.sha256'] and '__pycache__' not in p.parts}
    dump(root/'inventory.json',{'scope':'authorized documentary OE3 closure; functional pilots; zero formal 2-vCPU trials; no strict URLLC certification','files':files})
    (root/'inventory.sha256').write_text(digest(root/'inventory.json')+'  inventory.json\n')
    with zipfile.ZipFile(ARCHIVE,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*files,'inventory.json','inventory.sha256']:z.write(root/name,'ieee-2vcpu/'+name)
    ARCHIVE.with_suffix('.zip.sha256').write_text(digest(ARCHIVE)+'  '+ARCHIVE.name+'\n')
    return verify_zip(ARCHIVE)


def verify_zip(archive):
    sha=digest(archive);assert archive.with_suffix('.zip.sha256').read_text().split()[0]==sha
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        raw=z.read('ieee-2vcpu/inventory.json');manifest=json.loads(raw)
        assert hashlib.sha256(raw).hexdigest()==z.read('ieee-2vcpu/inventory.sha256').decode().split()[0]
        assert set(z.namelist())=={'ieee-2vcpu/'+n for n in [*manifest['files'],'inventory.json','inventory.sha256']}
        for name,h in manifest['files'].items():assert hashlib.sha256(z.read('ieee-2vcpu/'+name)).hexdigest()==h
    return {'archive':str(archive),'sha256':sha,'verified_files':len(manifest['files'])}


def reproduce(root,out):
    assert not out.exists() and not out.is_relative_to(root)
    raw=(root/'inventory.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==(root/'inventory.sha256').read_text().split()[0]
    for name,h in json.loads(raw)['files'].items():
        p=(root/name).resolve();assert p.is_relative_to(root) and digest(p)==h,name
    summary,packets,warmups,prior=calculate(root)
    assert summary==read(root/'evidence/consolidated-summary.json')
    (out/'datasets').mkdir(parents=True)
    csv_write(out/'datasets/all-pilots.csv',summary['pilot_rows']);csv_write(out/'datasets/prior-1vcpu-cpu-runs.csv',prior)
    figures(root,out,summary,packets);dump(out/'summary.json',summary)
    return {'reproduced_exactly':True,'pilot_runs':8,'prior_cpu_runs':len(prior),'output':str(out)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=DEST)
    p.add_argument('--seal',action='store_true');p.add_argument('--verify-zip',type=Path);p.add_argument('--reproduce',type=Path);a=p.parse_args()
    if a.verify_zip:result=verify_zip(a.verify_zip.resolve())
    elif a.reproduce:result=reproduce(a.root.resolve(),a.reproduce.resolve())
    else:
        result=build(a.root.resolve())
        if a.seal:result=seal(a.root.resolve())
    print(json.dumps(result))
