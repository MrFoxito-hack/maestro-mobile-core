"""Recompute packaged inference and figures without SSH or the original workspace."""
import argparse
import hashlib
import json
from pathlib import Path
from c8_2vcpu_stats import analyze_rows
from c8_ieee_stats import load_trials
from c8_thesis_plots import style, urllc_figures, csv_write
from c8_ieee_plots import extended_figures


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve();output=args.output.resolve()
    assert output!=root and not output.is_relative_to(root), 'write outside sealed evidence'
    assert not output.exists(), 'do not overwrite previous reproduction'
    inventory=json.loads((root/'inventory.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((root/'inventory.json').read_bytes()).hexdigest()==(root/'inventory.sha256').read_text().split()[0]
    for name,expected in inventory['files'].items():
        path=(root/name).resolve()
        assert path.is_relative_to(root)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected, name
    rows=load_trials(root);analysis=analyze_rows(rows)
    published=json.loads((root/'evidence/statistics_ieee.json').read_text(encoding='utf-8'))
    assert analysis==published['extended'], 'statistics differ; check pinned numerical versions'
    raws=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'runs').glob('*/001-*-raw.json'))]
    assert len(raws)==40 and len(rows)==40
    for name in ['evidence','datasets','figures']:(output/name).mkdir(parents=True)
    (output/'evidence/statistics_ieee.json').write_text(json.dumps(published,indent=2),encoding='utf-8')
    csv_write(output/'datasets/ieee_trials.csv',rows)
    style();urllc_figures(output/'figures',rows,raws);extended_figures(output,rows,raws)
    print(json.dumps({'files_verified':len(inventory['files']),'runs':40,'statistics_exactly_reproduced':True,'output':str(output)}))


if __name__=='__main__':main()
