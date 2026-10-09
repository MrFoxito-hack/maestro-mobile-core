"""Consolidate certified N20 evidence without pooling preliminary URLLC trials."""
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from c8_thesis_plots import csv_write

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/c8-campaign'


def main():
    ext=BASE/'ieee';status=json.loads((ext/'evidence/certification-status.json').read_text())
    assert status['accepted'] and status['formal_trials']=={'urllc':40} and status['formal_packets']==60000
    old=json.loads((BASE/'evidence/certification-status.json').read_text());assert old['accepted']
    backup=ROOT/'.work/c8-campaign-baseline-v1-certified.zip'
    assert hashlib.sha256(backup.read_bytes()).hexdigest()=='ea40608970a94218875a880e5ed824c0b8e2d53286463a35f58f3e91d7bba3b7'
    # All original measurements and analytical tables remain byte-identical.
    with zipfile.ZipFile(backup) as z:
        manifest=json.loads(z.read('c8-campaign/evidence/inventory.json'))
        for f in manifest['files']:
            if f['path'].startswith(('runs/','datasets/','figures/')):
                assert hashlib.sha256((BASE/f['path']).read_bytes()).hexdigest()==f['sha256'], f['path']
    with (BASE/'datasets/trials.csv').open(encoding='utf-8',newline='') as f:
        baseline_rows=[r for r in csv.DictReader(f) if r['experiment']!='urllc']
    with (ext/'datasets/ieee_trials.csv').open(encoding='utf-8',newline='') as f:extended_rows=list(csv.DictReader(f))
    rows=baseline_rows+extended_rows;assert len(rows)==100
    csv_write(BASE/'datasets/trials_v2_consolidated.csv',rows)
    with (BASE/'datasets/packets_v2_consolidated.csv').open('w',encoding='utf-8',newline='') as out:
        writer=None;count=0
        for source,omit_urllc in [(BASE/'datasets/packets.csv',True),(ext/'datasets/packets.csv',False)]:
            with source.open(encoding='utf-8',newline='') as f:
                reader=csv.DictReader(f)
                if writer is None:writer=csv.DictWriter(out,fieldnames=reader.fieldnames);writer.writeheader()
                for r in reader:
                    if omit_urllc and r['experiment']=='urllc':continue
                    writer.writerow(r);count+=1
    assert count==147720
    shutil.copyfile(ext/'evidence/certification-status.json',BASE/'evidence/certification-status-ieee.json')
    release={'created_at':datetime.now(timezone.utc).isoformat(),'accepted':True,'release':'C8 v2 / URLLC N20',
        'formal_trials':{'miot':24,'isolation':12,'urllc':40},'formal_trial_count':76,'formal_streams':100,
        'formal_packets':count,'preliminary_urllc_trials_used_for_comparison_only':12,
        'baseline_backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
        'acquisition_certificates':['evidence/certification-status.json','ieee/evidence/certification-status.json'],
        'primary_joint_criterion_met':status['primary_joint_criterion_met'],
        'current_datasets':['datasets/trials_v2_consolidated.csv','datasets/packets_v2_consolidated.csv'],
        'urllc_figures_directory':'ieee/figures','urllc_analysis':'ieee/evidence/statistics_ieee.json',
        'no_pooling_preliminary_and_extended_urllc':True}
    (BASE/'evidence/campaign-index.json').write_text(json.dumps(release,indent=2)+'\n')
    report=(ext/'evidence/chapter4_results_ieee.md').read_text(encoding='utf-8')
    certification='\n\n## Certificación y paquete vigente\n\n'
    certification+='Adquisición N20 aceptada por c8_validate.py --profile ieee; 40 corridas y 60000 intentos. '
    certification+='El inventario vigente combina 24 MIoT, 12 aislamiento y 40 URLLC: 76 corridas, 100 flujos y 147720 intentos. '
    certification+='Las doce URLLC de N6 se conservan únicamente para comparación. '
    certification+=f"Contadores XDP: UL_OK={status['bpf_counter_deltas']['UL_OK']}, DL_OK={status['bpf_counter_deltas']['DL_OK']}; "
    certification+=f"fracción rápida {status['xdp_fast_share_pct']['ul']:.4f}% UL y {status['xdp_fast_share_pct']['dl']:.4f}% DL incluyendo calentamientos. "
    certification+='Mismo proceso, ambas PDU y QER durante las mediciones; baseline restaurado al finalizar. '
    certification+='Los 785 archivos C0–C7 permanecen sin cambios. El cierre acredita adquisición y reproducibilidad, no aceptación editorial IEEE.\n'
    text=report.rstrip()+certification
    for p in [ext/'evidence/chapter4_results_ieee.md',BASE/'evidence/chapter4_results_ieee.md',ROOT/'docs/C8_URLLC_IEEE_N20.md']:
        p.write_text(text,encoding='utf-8')
    inference=json.loads((ext/'evidence/statistics_ieee.json').read_text())
    lines=['','', '## Extensión C8 v2: URLLC N=20', '',
           'Adquisición ampliada cerrada y auditada: 20 bloques, 40 corridas de 15 s y 60000 intentos; '
           'lote separado del N=6 certificado. [Reporte completo, IC y contrastes](C8_URLLC_IEEE_N20.md).', '',
           '**Criterio conjunto preregistrado:** '+('cumplido.' if status['primary_joint_criterion_met'] else 'no cumplido.'), '',
           '| Métrica | Δ XDP−Kernel | BCa 95% | p t unilateral Holm | p Wilcoxon unilateral Holm |',
           '|---|---:|---|---:|---:|']
    for m in ['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms']:
        r=inference['extended']['metrics'][m];ci=r['bca']['95'];label='no definido' if ci is None else f'[{ci[0]:.4f}; {ci[1]:.4f}]'
        lines.append(f"| {m} | {r['difference']:.4f} | {label} | {r['t']['p_less_holm_primary']:.6g} | {r['wilcoxon']['p_less_holm_primary']:.6g} |")
    lines+=['','El paquete vigente contiene 24 MIoT + 12 aislamiento + 40 URLLC (76 corridas; 147720 intentos). '
            'Las cifras N=6 anteriores se conservan como antecedente histórico, sin mezclar sus doce corridas '
            'con la extensión. El ZIP baseline-v1-certified.zip conserva íntegro el cierre v1. '
            'El índice vigente es `.work/c8-campaign/evidence/campaign-index.json`; figuras nuevas en `ieee/figures/`.', '']
    addition='\n'.join(lines)
    for name in ['C8_PROTOCOLO_EXPERIMENTAL.md','PLAN_CIERRE_TESIS_MAESTRO_5G.md']:
        p=ROOT/'docs'/name;original=p.read_bytes().decode('utf-8')
        original=original.split('\n\n## Extensión C8 v2: URLLC N=20',1)[0]
        p.write_bytes((original.rstrip()+addition).encode('utf-8'))
    print(json.dumps(release))


if __name__=='__main__':main()
