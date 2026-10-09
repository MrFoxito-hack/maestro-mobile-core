"""Close a failed latency pilot honestly; never certify or start formal N=20."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from c8_thesis_plots import csv_write, style, save

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'.work/c8-campaign/ieee-2vcpu'


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    disposition=json.loads((DEST/'evidence/pilot-disposition.json').read_text())
    assert disposition['formal_runs']==0 and not disposition['pilot_gate_passed']
    assert not list((DEST/'runs').glob('*/campaign.json'))
    assert not json.loads((DEST/'setup/user-stop-after-pilot.json').read_text())['formal_runs_authorized_after_failed_pilot']
    rows=json.loads((DEST/'pilot/diagnostic.json').read_text())
    assert len(rows)==2 and {r['mode'] for r in rows}=={'kernel','xdp'}
    datasets=DEST/'pilot/datasets';datasets.mkdir(exist_ok=True)
    figures=DEST/'pilot/figures';figures.mkdir(exist_ok=True)
    fields=['mode','attempted','received','rtt_mean_ms','rtt_p50_ms','rtt_p95_ms','rtt_p99_ms','max_rtt_ms','over10ms','sender_lateness_p99_ms','sender_lateness_max_ms','process_cpu_us_per_urr_packet']
    csv_write(datasets/'pilot-summary.csv',[{k:r[k] for k in fields} for r in rows])
    packets=[];warmups=[];samples={}
    for path in sorted((DEST/'pilot/runs').glob('*/campaign.json')):
        campaign=json.loads(path.read_text());trial,=campaign['trials']
        raw=json.loads((path.parent/(trial['name']+'-raw.json')).read_text());stream,=raw['streams']
        mode=raw['configuration']['mode'];samples[mode]=stream['packets']
        packets += [{'mode':mode,**p} for p in stream['packets']]
        warmups += [{'mode':mode,**p} for p in json.loads((path.parent/'excluded-warmup-raw.json').read_text())['packets']]
    assert len(packets)==3000 and len(warmups)==1000
    csv_write(datasets/'pilot-packets.csv',packets);csv_write(datasets/'excluded-warmup-packets.csv',warmups)
    style();fig,axes=plt.subplots(1,2,figsize=(7,3.2))
    for mode,color in [('kernel','#0072B2'),('xdp','#D55E00')]:
        ps=samples[mode];x=np.sort([p['rtt_ms'] for p in ps if p['ack']])
        axes[0].step(x,np.arange(1,len(x)+1)/len(x),where='post',label=mode,color=color)
        axes[1].plot([p['sequence'] for p in ps],[(p['sent_ns']-p['scheduled_ns'])/1e6 for p in ps],label=mode,color=color,linewidth=.6)
    axes[0].axvline(10,color='black',linestyle='--',linewidth=.8)
    axes[0].set(xlabel='Application RTT [ms]',ylabel='Empirical CDF',title='Full pilot RTT range',xlim=(0,None));axes[0].legend()
    axes[1].set(xlabel='Packet sequence',ylabel='Sender lateness [ms]',title='Observed scheduling delay');axes[1].legend()
    fig.suptitle('Pilot only: one run per arm; no inferential confidence intervals',fontsize=9)
    save(fig,figures/'pilot_rtt_and_sender_lateness')
    lines=['# OE3 2-vCPU: piloto no aprobado; N=20 detenido','',
           '**Resultado:** 3000/3000 respuestas en el piloto, XDP 1500/1500 paquetes UL y DL. El requisito previo de ausencia de picos de 10 ms no se cumplió. El usuario decidió mantenerlo y detener la adquisición formal: **0/40 corridas y 0/60000 paquetes formales**. No existe certificación OE3 nueva.','',
           '| Brazo piloto | ACK | Media RTT [ms] | p50 [ms] | p95 [ms] | p99 [ms] | Máximo [ms] | RTT ≥10 ms |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['mode']} | {r['received']}/{r['attempted']} | {r['rtt_mean_ms']:.4f} | {r['rtt_p50_ms']:.4f} | {r['rtt_p95_ms']:.4f} | {r['rtt_p99_ms']:.4f} | {r['max_rtt_ms']:.4f} | {r['over10ms']} |")
    lines += ['','Estas cifras describen una corrida por brazo. No constituyen N=20 ni permiten afirmar significancia, potencia o cumplimiento del criterio conjunto. No se calcularon IC BCa, t pareado ni Wilcoxon con una sola pareja. Los 1000 paquetes de warmup se conservan aparte.','',
              '## Estado operativo al cierre','',
              '- Core=1, UPF1=2, UPF2=1, gNB=2, UE=2 vCPU; ocho vCPU totales, con aprobación del usuario para Core=1.',
              '- Máscaras de los procesos ejecutores sobre ocho P-cores distintos; no implica reserva exclusiva frente a Windows ni prueba aislamiento de vCPU físicas bajo Hyper-V/NEM.',
              '- Windows **Normal** en los cinco ejecutores `suplib-3rdchild`, autorizado explícitamente antes del piloto. Los lanzadores estaban High. Se corrigió el falso positivo del script que excluía todos los `suplib-`.',
              '- Hilos críticos UPF/MEC/gNB/UE en SCHED_RR 10 y vCPU1, verificados antes/después de ambas corridas.',
              '- Durante el piloto: irqbalance detenido temporalmente, IRQ de NIC verificadas en vCPU0, RPS hacia vCPU1. Esta configuración no demuestra que todos los eventos XDP ocurran exclusivamente en vCPU1 ni reserva vCPU0 para todo el OS.',
              '- Teardown verificado: Kernel restaurado, QoS reaplicado, IRQ e irqbalance restaurados, seis sesiones nativas y canario final 200/200 ACK.',
              '- Los 785 archivos C0–C7, las raíces `ieee/` y `ieee-rr/`, y los ZIP anteriores permanecen sin cambios.','',
              '## Incidencias conservadas','',
              'El primer canario de esta reanudación obtuvo 0/200 ACK: el gNB notificó PDU session not found pese a las sesiones NAS/UPF observadas. Se guardaron los datos fallidos, se reiniciaron los dos UE vehiculares y se reaplicaron QoS y RR; el siguiente canario pasó 200/200.','',
              'Un intento de preparación falló al comprobar inmediatamente la afinidad IRQ efectiva. Se restauró el estado y se separó la comprobación en una llamada posterior, cuando la migración ya había sido efectiva. Ese intento no adquirió piloto ni datos formales y quedó archivado en `setup/preparation-attempt-01/`.','',
              'Los picos del piloto están acompañados por retrasos del emisor de hasta 57,60 ms (Kernel) y 86,65 ms (XDP). Son observaciones compatibles con perturbaciones temporales, pero no identifican por sí solas una causa. No se atribuyen exclusivamente a Hyper-V, al UPF o a XDP. Se conserva el rango completo en las figuras.','',
              '## Alcance del sello','',
              'El paquete `c8-campaign-oe3-2vcpu-pilot-evidence.zip` contiene preparación, fallos, ambos raw del piloto, warmup, CSV, figuras PNG/SVG/PDF a 300 DPI, código y evidencias de restauración. Su manifiesto SHA-256 acredita integridad de archivos, no éxito experimental ni certificación IEEE. No se genera el ZIP formal N=20.','',
              'El prerregistro original y su hash permanecen intactos. `setup/user-stop-after-pilot.json` registra la decisión posterior del usuario; el requisito del piloto no se eliminó. Cualquier nueva campaña requiere una decisión explícita y un protocolo que preserve este intento.','',
              'Los scripts de análisis N=20 están preparados pero no han sido ejecutados/validados contra una campaña de 40 corridas. Las siete pruebas de Pratt exacto pasaron.','',
              'Reproducir las tablas y figuras del piloto: `backend\\.venv\\Scripts\\python.exe infra\\c8_2vcpu_close_pilot.py`. El comando conserva un ZIP existente y verifica su integridad; no lo sobrescribe.','']
    report='\n'.join(lines)
    for path in [ROOT/'docs/C8_OE3_URLLC_2VCPU.md',DEST/'evidence/pilot-report.md']:path.write_text(report,encoding='utf-8')
    source=DEST/'closure-source';source.mkdir(exist_ok=True)
    for name in ['c8_2vcpu_close_pilot.py','c8_thesis_plots.py','c8_2vcpu_stats.py','c8_2vcpu_campaign.py','c8_2vcpu_irq.py','c8_2vcpu_scheduler.py','c8_2vcpu_traffic.py','c8_2vcpu_report.py','c8_2vcpu_pin.ps1']:
        shutil.copyfile(ROOT/'infra'/name,source/name)
    archive=ROOT/'.work/c8-campaign-oe3-2vcpu-pilot-evidence.zip'
    if not archive.exists():
        files={p.relative_to(DEST).as_posix():digest(p) for p in sorted(DEST.rglob('*')) if p.is_file() and p.name not in ['pilot-inventory.json','pilot-inventory.sha256'] and '__pycache__' not in p.parts}
        inventory=DEST/'pilot-inventory.json';inventory.write_text(json.dumps({'scope':'failed pilot evidence, zero formal trials; no success certification','files':files},indent=2)+'\n')
        (DEST/'pilot-inventory.sha256').write_text(digest(inventory)+'  pilot-inventory.json\n')
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name in [*files,'pilot-inventory.json','pilot-inventory.sha256']:z.write(DEST/name,'ieee-2vcpu/'+name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        manifest=json.loads(z.read('ieee-2vcpu/pilot-inventory.json'))
        for name,h in manifest['files'].items():assert hashlib.sha256(z.read('ieee-2vcpu/'+name)).hexdigest()==h
    seal=archive.with_suffix('.zip.sha256')
    if seal.exists():assert seal.read_text().split()[0]==digest(archive)
    else:seal.write_text(digest(archive)+'  '+archive.name+'\n')
    print(json.dumps({'pilot_archive':str(archive),'sha256':digest(archive),'verified_members':len(manifest['files']),'formal_trials':0}))


if __name__=='__main__':main()
