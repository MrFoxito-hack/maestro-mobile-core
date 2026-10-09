"""Produce thesis-ready C8 tables from certified measurements; no network access."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(root):
    analysis = json.loads((root/'datasets/analysis.json').read_text(encoding='utf-8'))
    certification = json.loads((root/'evidence/certification-status.json').read_text(encoding='utf-8'))
    assert certification['accepted']
    groups = analysis['groups']
    rows = []
    for experiment, levels, slices in [
        ('urllc', ['kernel', 'xdp'], ['urllc']),
        ('miot', ['10', '50', '100', '1000'], ['miot']),
        ('isolation', ['idle', 'loaded'], ['urllc', 'embb', 'miot']),
    ]:
        for level in levels:
            for slice_name in slices:
                g, = [g for g in groups if (g['experiment'], g['level'], g['slice']) ==
                      (experiment, level, slice_name)]
                rows.append({
                    'experimento': experiment, 'condicion': level, 'slice': slice_name,
                    'n_corridas': g['n_runs'],
                    **{k: g[k]['mean'] for k in ['rtt_mean_ms', 'rtt_p50_ms', 'rtt_p95_ms', 'rtt_p99_ms',
                                               'jitter_mean_ms', 'deadline_miss_pct', 'receiver_pps']},
                    'perdida_pct': 100-g['delivery_pct']['mean'],
                })
    with (root/'datasets/chapter4_summary.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        '## Resultados finales medidos de C8', '',
        'Corte: 2026-10-07. Adquisición aceptada: 24 corridas MIoT, 12 de aislamiento y '
        '12 URLLC; 48 corridas, 72 flujos y '
        f"{certification['formal_packets']:,} intentos de paquete, excluyendo calentamientos y pilotos.", '',
        'La tabla presenta la **media de los estadísticos calculados por corrida**, con seis '
        'corridas por fila. No son percentiles de una muestra agrupada. La CDF sí agrupa los '
        'ACK y sus anotaciones p95/p99 corresponden a esa distribución. RTT y jitter están '
        'en ms; pérdida e incumplimiento del plazo RTT <5 ms están en porcentaje.', '',
        '| Experimento | Condición | Slice | n | RTT medio | RTT p50 | RTT p95 | RTT p99 | Jitter medio | Pérdida % | Plazo incumplido % | Recibidos pps |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        lines.append('| ' + ' | '.join([
            r['experimento'], r['condicion'], r['slice'], str(r['n_corridas']),
            *[f'{r[k]:.3f}' for k in ['rtt_mean_ms', 'rtt_p50_ms', 'rtt_p95_ms', 'rtt_p99_ms',
                                    'jitter_mean_ms', 'perdida_pct', 'deadline_miss_pct', 'receiver_pps']],
        ]) + ' |')
    lines += ['', '### Efectos pareados e incertidumbre', '',
              'IC bootstrap percentil al 95%, 10000 remuestras de seis diferencias por bloque, '
              'semilla 42017. Se conserva la precisión exploratoria del protocolo.', '',
              '| Contraste | Variable | Diferencia media | IC 95% |',
              '|---|---|---:|---|']
    for experiment, label in [('urllc', 'XDP − Kernel'), ('isolation', 'Loaded − Idle')]:
        for metric, title in [('rtt_p99_ms', 'RTT p99 [ms]'), ('jitter_mean_ms', 'Jitter [ms]'),
                              ('deadline_miss_pct', 'Incumplimiento [p.p.]'), ('delivery_pct', 'Entrega [p.p.]')]:
            e = analysis['paired_effects'][experiment][metric]
            lo, hi = e['ci95']
            lines.append(f"| {label} | {title} | {e['difference_treatment_minus_control']:.3f} | [{lo:.3f}, {hi:.3f}] |")
    effects = analysis['paired_effects']['urllc']
    p99, jitter = effects['rtt_p99_ms'], effects['jitter_mean_ms']
    supported = p99['ci95'][1] < 0 and jitter['ci95'][1] < 0
    lines += ['', ('El IC de ambos efectos respalda una reducción de p99 y jitter en esta campaña.'
                   if supported else 'La campaña no demuestra conjuntamente una reducción de p99 y jitter '
                   'con IC al 95% completamente negativos. Se publican los efectos observados sin '
                   'convertir el cierre de adquisición en aceptación de la hipótesis.'), '',
              'La no inferioridad exploratoria original (incremento p99 ≤1 ms; jitter ≤0,5 ms) '
              'se interpreta mediante los IC anteriores y se distingue del criterio OE4 de pérdida y RTT medio. '
              f"Hay {sum(r['run_id'].endswith('-isolation-kernel') for r in analysis['urr_endpoint_mismatches'])} "
              'discrepancias URR/ACK en aislamiento, conservadas en analysis.json: '
              'el cálculo sin ajustar compara intentos del socket (anteriores a tc) con el punto UPF. '
              'Para OE4 QoS, la conciliación corregida por admisión se detalla abajo. '
              'Las campañas independientes URLLC y MIoT conservan su conciliación original.', '',
              'Los 1000 sensores MIoT son identidades de aplicación sobre una PDU: '
              '200 mensajes/s ofrecidos, dos mensajes por sensor y corrida; no 1000 registros NAS.', '',
              '### QoE eMBB: evidencia C6 consolidada', '',
              'Las cuatro grabaciones originales de C6 se conservan sin modificarlas. '
              'P.1203 proviene de sus trazas de reproducción; el ensayo UDP de aislamiento '
              'no se convierte en MOS. Son experimentos distintos.', '',
              '| Condición | Grabaciones | MOS P.1203 medio | IC t 95%, gl=1 |',
              '|---|---:|---:|---|']
    with (root/'datasets/qoe.csv').open(encoding='utf-8', newline='') as f:
        qoe = list(csv.DictReader(f))
    for prefix, label in [('baseline', 'Sin congestión'), ('congestion', 'Con congestión')]:
        r = next(r for r in qoe if r['phase'].startswith(prefix))
        lines.append(f"| {label} | 2 | {float(r['condition_mean']):.3f} | "
                     f"[{float(r['ci95_low']):.3f}, {float(r['ci95_high']):.3f}] |")
    lines += ['', 'Los IC t son exploratorios y no se recortan al rango de la escala MOS.', '',
              '### Auditoría de adquisición y restauración', '',
              f"- Contadores BPF: UL_OK={certification['bpf_counter_deltas']['UL_OK']}, "
              f"DL_OK={certification['bpf_counter_deltas']['DL_OK']}; coinciden con los deltas del controlador.",
              f"- Fracción rápida XDP incluyendo calentamientos: UL "
              f"{certification['xdp_fast_share_pct']['ul']:.3f}%, DL "
              f"{certification['xdp_fast_share_pct']['dl']:.3f}%. La renovación PFCP puede cerrar "
              'brevemente el paso XDP y conservar el forwarding nativo.',
              '- Mismo proceso/PDU/ruta/QER entre brazos; doce comprobaciones finales con '
              'ambos vehículos PS-ACTIVE. Se exige identidad estable en todos los snapshots '
              'de medición y en las renovaciones del controlador. No se instrumentó un monitor NAS continuo.',
              '- Ocho corridas anteriores excluidas por interrupción de lote/instrumentación, '
              'sin selección por RTT; datos y motivos preservados en setup/exclusions.json.',
              '- Preparación adicional abortada antes de medir por discrepancia NAS/PFCP; '
              'recuperación acotada incorporada exclusivamente en C8 y verificada por prueba.',
              '- Baseline UPF, dos PDU y acceso MEC restaurados; configuración SMF2 restaurada '
              'byte a byte y política charging igual a la original. Se conservan la recarga '
              'experimental explícita y los consumos reales.',
              '- Figuras PDF/SVG y PNG a 300 DPI en figures/; CSV consolidados en datasets/ '
              'y copia en evidence/datasets/ al empaquetar. inventory.json e inventory.sha256 '
              'sellan los archivos; verification.json registra la verificación.', '',
              'Reproducción local, con el virtualenv backend: c8_thesis_plots.py, c8_validate.py, '
              'c8_report.py y c8_package.py. La verificación de integridad del paquete existente '
              'usa c8_package.py --verify y no regenera resultados.', '']
    report = '\n'.join(lines)
    oe4_path = root/'evidence/oe4-acceptance.json'
    if oe4_path.exists():
        oe4 = json.loads(oe4_path.read_text())
        report += '\n### OE4: aislamiento con admisión limitada y prioridad Linux\n\n'
        report += ('Criterio medido cumplido.' if oe4['accepted'] else 'Criterio medido pendiente.') + ' '
        report += f"URLLC loaded: {oe4['loaded_urllc_ack']}/{oe4['loaded_urllc_attempted']} ACK, pérdida {oe4['loaded_urllc_loss_pct']:.3f}%, RTT medio {oe4['loaded_urllc_rtt_mean_ms']:.3f} ms.\n\n"
        report += 'Se ofrecieron 100/500/500 pps (URLLC/eMBB/MIoT). TBF antes de UERANSIM limita eMBB a 500 kbit/s y MIoT a 40 kbit/s; PRIO clasifica los puertos RLS por UE en el transporte. SCHED_RR 10 prioriza los procesos URLLC, gNB, UPF3 y eco MEC. La saturación se acredita con descartes/overlimits de TBF, no con falta de saldo.\n\n'
        if oe4.get('all_36_streams_admitted_urr_ack_reconciled'):
            report += 'Los 36 flujos concilian admisión/ACK/URR: ACK = intentos menos descartes tc; URR = admitidos UL + ACK DL según las direcciones PDR. Los contadores tc loaded concilian exactamente. Las 12 diferencias del cálculo sin ajustar son descartes deliberados antes de UPF en eMBB/MIoT loaded. En idle se registró un paquete adicional de interfaz eMBB, fuera de los ACK y URR de prueba; se conserva como delta no atribuido, sin exigir igualdad del contador global con los probes en ese caso.\n\n'
        report += '| Bloque loaded | ACK URLLC | RTT medio ms | Máximo ms | Plazo incumplido % | Descartes tc eMBB |\n|---:|---:|---:|---:|---:|---:|\n'
        for trial in oe4['trials']:
            if trial['level'] != 'loaded': continue
            u = trial['slices']['urllc']; e = trial['slices']['embb']
            report += f"| {trial['block']} | {u['ack']}/{u['attempted']} | {u['rtt_mean_ms']:.3f} | {u['rtt_max_ms']:.3f} | {u['deadline_miss_pct']:.3f} | {e['tc_drops']} |\n"
        report += '\nLa campaña anterior se conserva en runs/, historical_isolation_trials.csv y el ZIP baseline-v1-certified; no se mezcla con este tratamiento. Todos los bloques nuevos se incluyen. La media <5 ms no implica un máximo determinista ni una certificación 5QI/URLLC de radio. 5QI=1/2 no se configura ni se equipara a esta política Linux.\n'
    (root/'evidence/chapter4_results.md').write_text(report, encoding='utf-8')
    print(json.dumps({'summary_rows':len(rows), 'report':str(root/'evidence/chapter4_results.md')}))
    return report


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=ROOT/'.work/c8-campaign')
    parser.add_argument('--ieee',action='store_true')
    args=parser.parse_args()
    if args.ieee:
        from c8_report_ieee import build as build_ieee
        build_ieee(args.root)
    else:build(args.root)
