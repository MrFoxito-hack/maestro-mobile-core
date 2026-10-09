"""Report all preregistered C8 endpoints, including unfavorable/null results."""
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import numpy as np
from c8_ieee_stats import METRICS,PRIMARY,load_trials,analyze_rows

LABELS={'rtt_mean_ms':'RTT medio [ms]','rtt_p50_ms':'RTT p50 [ms]','rtt_p95_ms':'RTT p95 [ms]',
        'rtt_p99_ms':'RTT p99 [ms]','jitter_mean_ms':'Jitter [ms]','deadline_miss_pct':'Plazo incumplido [p.p.]'}


def write_csv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def build(root):
    evidence=root/'evidence';evidence.mkdir(exist_ok=True)
    datasets=root/'datasets';datasets.mkdir(exist_ok=True)
    protocol=json.loads((root/'setup/preregistered-design.json').read_text())
    acquisition=json.loads((root/'setup/acquisition.json').read_text())
    assert acquisition['status']=='completed' and len(acquisition['completed'])==40
    window=json.loads((root/'setup/xdp-window.json').read_text())
    assert window['mode']=='restore' and window['restored']
    rows=load_trials(root);assert len(rows)==40
    previous_rows=load_trials(root.parent);assert len(previous_rows)==12
    current=analyze_rows(rows);previous=analyze_rows(previous_rows)
    assert all(x['n_pairs']==20 for x in current['metrics'].values())
    pooled=[]
    for mode in ['kernel','xdp']:
        values=[]
        for p in sorted((root/'runs').glob(f'*-{mode}/*-raw.json')):
            raw=json.loads(p.read_text())
            if raw['configuration']['warmup']:continue
            values += [x['rtt_ms'] for x in raw['streams'][0]['packets'] if x['ack']]
        relevant=[r for r in rows if r['mode']==mode]
        pooled.append({'mode':mode,'runs':20,'attempted':sum(r['attempted'] for r in relevant),
            'ack':len(values),'mean_ms':float(np.mean(values)),'sd_ms':float(np.std(values,ddof=1)),
            **{f'p{p}_ms':float(np.quantile(values,p/100,method='linear')) for p in [50,95,99]},
            'loss_pct':100*(1-len(values)/sum(r['attempted'] for r in relevant))})
    write_csv(datasets/'ieee_trials.csv',rows)
    flat=[]
    for m,r in current['metrics'].items():
        flat.append({'metric':m,'primary':m in PRIMARY,'n_pairs':r['n_pairs'],
            **{k:r[k] for k in ['kernel_mean','kernel_sd','xdp_mean','xdp_sd','difference','difference_sd','cohen_dz']},
            **{f'bca{level}_{edge}':r['bca'][level][i] if r['bca'][level] else None
               for level in ['95','99'] for i,edge in enumerate(['low','high'])},
            **{f'{family}_{key}':r[family].get(key) for family in ['t','wilcoxon']
               for key in ['p_less','p_two_sided','p_less_holm_primary']},
            'shapiro_p':r['shapiro_p'],'lag1_correlation':r['lag1_correlation']})
    write_csv(datasets/'ieee_statistics.csv',flat);write_csv(datasets/'ieee_pooled_rtt.csv',pooled)
    comparison=[]
    for m in METRICS:
        a,b=previous['metrics'][m],current['metrics'][m]
        comparison.append({'metric':m,'n6_difference':a['difference'],'n20_difference':b['difference'],
            'n6_bca95':a['bca']['95'],'n20_bca95':b['bca']['95'],
            'n6_bca95_width':None if a['bca']['95'] is None else a['bca']['95'][1]-a['bca']['95'][0],
            'n20_bca95_width':None if b['bca']['95'] is None else b['bca']['95'][1]-b['bca']['95'][0]})
    order_sensitivity={}
    for m in PRIMARY:
        differences=current['metrics'][m]['differences']
        order_sensitivity[m]={label:float(np.mean([d for d,order in zip(differences,protocol['order']) if order[0]==first]))
                              for label,first in [('Kernel_first','kernel'),('XDP_first','xdp')]}
    inputs={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root/'runs').glob('*/*.json'))}
    report={'created_at':datetime.now(timezone.utc).isoformat(),'extended':current,'preliminary_recomputed_bca':previous,
            'pooled_descriptive_only':pooled,'comparison':comparison,'order_sensitivity_descriptive':order_sensitivity,
            'deadline_equal_5ms_packets':sum(r['equal_5ms_count'] for r in rows),
            'deadline_gt5_by_mode':{m:float(np.mean([r['deadline_gt5_pct'] for r in rows if r['mode']==m])) for m in ['kernel','xdp']},
            'input_sha256':inputs,'preregistration_sha256':hashlib.sha256((root/'setup/preregistered-design.json').read_bytes()).hexdigest(),
            'inference_is_not_acquisition_certification':True}
    (evidence/'statistics_ieee.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    fmt=lambda x:'no definido' if x is None else f'{x:.6g}'
    ci=lambda x:'no definido (degeneración)' if x is None else f'[{x[0]:.4f}; {x[1]:.4f}]'
    lines=['# C8: extensión URLLC de 20 bloques pareados','',
           'Diseño fijo: 20 bloques, 40 corridas de 15 s, 1500 paquetes por corrida; 60000 intentos formales. '
           'Diez órdenes AB y diez BA, semilla 42017. Calentamiento de 2 s por corrida excluido. '
           'Misma PDU, proceso v7, ruta MEC y QER entre brazos; XDP genérico en veth.', '',
           '**Dictamen del criterio conjunto preregistrado:** '+('cumplido.' if current['primary_joint_criterion_met'] else 'no cumplido.'), '',
           'Se exige para RTT medio, p50 y p95: IC BCa 95% enteramente negativo y p unilateral <0,01 '
           'tras Holm en ambas familias (t y Wilcoxon). La aceptación de adquisición no depende de ese dictamen.', '',
           '## Estimadores por corrida y diferencias pareadas','',
           'Cada media y DE describe 20 valores por brazo. Las unidades de replicación son los 20 bloques, '
           'no los paquetes. Δ = XDP − Kernel; un Δ negativo favorece XDP. Cohen dz usa la DE muestral de Δ.', '',
           '| Métrica | Kernel media ± DE | XDP media ± DE | Δ media | DE de Δ | Cohen dz | BCa 95% | BCa 99% |',
           '|---|---|---|---:|---:|---:|---|---|']
    for m,r in current['metrics'].items():
        lines.append(f"| {LABELS[m]} | {r['kernel_mean']:.4f} ± {r['kernel_sd']:.4f} | "
                     f"{r['xdp_mean']:.4f} ± {r['xdp_sd']:.4f} | {r['difference']:.4f} | {r['difference_sd']:.4f} | "
                     f"{fmt(r['cohen_dz'])} | {ci(r['bca']['95'])} | {ci(r['bca']['99'])} |")
    lines+=['','## Contrastes completos','',
            't de Student pareado, gl=19. Wilcoxon: distribución exacta por asignaciones de signo, '
            'rangos medios para empates, ceros descartados; diferencias redondeadas a 12 decimales '
            'solo para determinar rangos/empates. Requiere simetría e independencia de las diferencias; '
            'no es una prueba libre de supuestos ni contrasta necesariamente la misma media que t.', '',
            '| Métrica | t unilateral | t bilateral | t unilateral Holm | Wilcoxon unilateral | Wilcoxon bilateral | Wilcoxon unilateral Holm |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for m,r in current['metrics'].items():
        values=[r[f].get(k) for f in ['t','wilcoxon'] for k in ['p_less','p_two_sided','p_less_holm_primary']]
        lines.append('| '+LABELS[m]+' | '+' | '.join('—' if v is None else fmt(v) for v in values)+' |')
    lines+=['','Holm se aplica por separado a los tres endpoints principales en cada familia. '
            'p99, jitter y plazo son secundarios; sus p sin ajuste se reportan como exploratorios. '
            'Los IC BCa 95% y 99% son puntuales, no intervalos simultáneos. Se usan 10000 remuestras '
            'de bloques con los mismos índices para ambos brazos y endpoints, semilla fija. '
            'Distribuciones degeneradas producen IC BCa no definidos, sin sustituirlos por ceros.','',
            '## Comparación N=6 frente a N=20','',
            'Para comparar el método se recalcularon los IC de N=6 con BCa. El acta certificada original '
            'conserva sus IC bootstrap percentiles sin cambios. Son lotes distintos (10 s frente a 15 s), '
            'adquiridos en momentos distintos; no se agrupan ni se atribuye todo cambio al tamaño N.', '',
            '| Métrica | Δ N=6 | BCa 95% N=6 | Δ N=20 | BCa 95% N=20 | Cambio de ancho |',
            '|---|---:|---|---:|---|---:|']
    for c in comparison:
        width=None if c['n6_bca95_width'] is None or c['n20_bca95_width'] is None else c['n20_bca95_width']-c['n6_bca95_width']
        lines.append(f"| {LABELS[c['metric']]} | {c['n6_difference']:.4f} | {ci(c['n6_bca95'])} | "
                     f"{c['n20_difference']:.4f} | {ci(c['n20_bca95'])} | {fmt(width)} |")
    lines+=['','## Distribución agrupada descriptiva','',
            'Los siguientes percentiles agrupan ACK y corresponden a las anotaciones de la CDF; '
            'no son la media de percentiles por corrida ni se usan como réplicas para los contrastes.', '',
            '| Brazo | ACK/intentos | RTT medio | DE de RTT | p50 | p95 | p99 | Pérdida % |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for p in pooled:
        lines.append(f"| {p['mode']} | {p['ack']}/{p['attempted']} | "+' | '.join(f'{p[k]:.4f}' for k in ['mean_ms','sd_ms','p50_ms','p95_ms','p99_ms','loss_pct'])+' |')
    lines+=['','## Alcance, diagnóstico y reproducibilidad','',
            '- RTT medio es tiempo de ida y vuelta de aplicación; no tiempo interno de servicio UPF. '
            'CPU/paquete procede de deltas de proceso y VM, no de timestamps por paquete dentro del driver.',
            '- p95/p99 son descriptores de RTT; no certifican por sí mismos 3GPP TS 22.261 ni permiten '
            'atribuir la cola exclusivamente al driver. Tampoco se declara offload en hardware.',
            '- Se conserva el umbral inicial: pérdida o ACK con RTT ≥5 ms es incumplimiento. '
            f"Se encontraron {report['deadline_equal_5ms_packets']} ACK exactamente a 5 ms; "
            'la variante solicitada RTT >5 ms también consta en los CSV/JSON.',
            '- N=20 no garantiza potencia. La planificación usa efectos del piloto, con incertidumbre '
            'y supuestos explícitos, en setup/preregistered-design.json. No se informa potencia post hoc '
            'como prueba adicional de significancia.',
            '- Se reutiliza el mismo testbed virtualizado. Independencia/normalidad no se garantizan; '
            'Shapiro, correlación lag-1 y sensibilidad descriptiva al orden AB/BA se publican en statistics_ieee.json. '
            'Las tareas de orquestación/análisis del anfitrión también pueden influir en la VM.',
            '- La CDF incluye bandas bootstrap puntuales al 95% remuestreando bloques completos. '
            'Las 60000 muestras no se tratan como ensayos independientes. Todos los ACK y extremos se conservan.',
            '- Ninguna corrida se elimina por latencia, p-valor o efecto. La adquisición no se prolonga '
            'para obtener un resultado favorable. El respaldo baseline-v1-certified.zip conserva el acta N=6.', '',
            'Métodos: [t pareado SciPy](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.ttest_rel.html), '
            '[BCa SciPy](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.bootstrap.html), '
            '[Wilcoxon SciPy](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.wilcoxon.html). '
            'La implementación exacta con empates se contrasta con enumeración exhaustiva pequeña y con SciPy sin empates. '
            '[TS 22.261](https://www.etsi.org/deliver/etsi_TS/122200_122299/122261/17.16.00_60/ts_122261v171600p.pdf).','']
    text='\n'.join(lines)
    if 'scheduler' in protocol:
        text+='\n## Disciplina temporal de esta campaña\n\n'
        text+='SCHED_RR prioridad 10 en todos los hilos de UPF, MEC, gNB y ambos UE vehiculares, '
        text+='reaplicado tras preparar v7 y verificado antes/después de cada corrida. '
        text+='La afinidad efectiva se archiva; no se afirma aislamiento de núcleos físicos. '
        text+='El lote N20 anterior permanece íntegro en `ieee/`; este lote está en `'+root.name+'`. '
        text+='No se combinan sus paquetes ni se seleccionan bloques según el resultado. '
        text+='CPU se mide en microsegundos por paquete URR, no en ciclos de hardware: '
        text+='no se adquirieron contadores PMU ni se infieren ciclos desde una frecuencia nominal.\n'
    (evidence/'chapter4_results_ieee.md').write_text(text,encoding='utf-8')
    (root.parent/'evidence/chapter4_results_ieee.md').write_text(text,encoding='utf-8')
    print(json.dumps({'n_pairs':20,'primary_joint_criterion_met':current['primary_joint_criterion_met'],'report':str(evidence/'chapter4_results_ieee.md')}))
    return report


if __name__ == '__main__':
    import argparse
    from c8_remote import ROOT
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=ROOT / '.work/c8-campaign/ieee-rr')
    build(parser.parse_args().root.resolve())
