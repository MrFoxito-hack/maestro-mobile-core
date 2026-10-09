"""Pointwise paired-block bootstrap CDF bands and block-level diagnostics."""
import json
import numpy as np
import matplotlib.pyplot as plt
from c8_thesis_plots import COLORS,STYLES,save,csv_write


def cdf_band(samples, grid, indices):
    counts=np.array([np.searchsorted(np.sort(x),grid,side='right') for x in samples],dtype=float)
    sizes=np.array([len(x) for x in samples],dtype=float)
    weights=np.array([np.bincount(i,minlength=len(samples)) for i in indices])
    distribution=(weights@counts)/(weights@sizes)[:,None]
    return np.quantile(distribution,[.025,.975],axis=0,method='linear')


def extended_figures(root,rows,raws):
    data={m:{} for m in ['kernel','xdp']}
    for raw in raws:
        c=raw['configuration']
        if c['experiment']=='urllc':data[c['mode']][c['block']]=[p['rtt_ms'] for p in raw['streams'][0]['packets'] if p['ack']]
    assert all(set(v)==set(range(20)) for v in data.values())
    indices=np.random.default_rng(42017).integers(0,20,size=(10000,20))
    pooled={m:np.sort(np.concatenate([v[b] for b in range(20)])) for m,v in data.items()}
    maximum=max(x[-1] for x in pooled.values());minimum=min(x[0] for x in pooled.values())
    grid=np.unique(np.r_[0,np.linspace(0,10,201),np.geomspace(max(minimum,.001),maximum,400),maximum])
    fig,axes=plt.subplots(1,2,figsize=(7,3.35),sharey=True)
    annotations=[];bands=[]
    for i,mode in enumerate(['kernel','xdp']):
        name='Kernel' if mode=='kernel' else 'XDP';x=pooled[mode];y=np.arange(1,len(x)+1)/len(x)
        lower,upper=cdf_band([data[mode][b] for b in range(20)],grid,indices)
        for g,lo,hi in zip(grid,lower,upper):bands.append({'mode':mode,'rtt_ms':g,'pointwise_ci95_low':lo,'pointwise_ci95_high':hi,'resampling_unit':'paired block','resamples':10000})
        for ax in axes:
            ax.fill_between(grid,lower,upper,color=COLORS[i],alpha=.2)
            ax.step(x,y,where='post',color=COLORS[i],linestyle=STYLES[i][0],marker=STYLES[i][1],
                    markersize=2.3,markevery=max(1,len(x)//16),label=f'{name}: {len(x)} ACK')
        for p in [.95,.99]:
            q=float(np.quantile(x,p,method='linear'))
            axes[1].vlines(q,0,p,color=COLORS[i],linestyle=':',linewidth=.65)
            annotations.append(f'{name} p{round(p*100)} = {q:.3f} ms')
    for ax in axes:
        ax.axvline(5,color='0.3',linestyle='-.',linewidth=.7)
        ax.set(xlabel='RTT [ms]',ylim=(0,1.02))
    axes[0].set(xlim=(0,10),ylabel='Empirical CDF [1]',title='Detail: 0–10 ms; deadline 5 ms')
    axes[1].set(xlim=(0,maximum*1.02),title='Complete observed range')
    axes[0].legend(loc='lower right',fontsize=7)
    axes[1].text(.97,.12,'\n'.join(annotations),transform=axes[1].transAxes,ha='right',fontsize=7,
                 bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
    fig.suptitle('20 pairs; shading: pointwise 95% block-bootstrap CI',fontsize=9)
    save(fig,root/'figures/urllc_rtt_cdf')
    csv_write(root/'datasets/ieee_cdf_bands.csv',bands)
    report=json.loads((root/'evidence/statistics_ieee.json').read_text())
    fig,axes=plt.subplots(1,3,figsize=(7,2.9))
    for ax,metric,label in zip(axes,['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms'],['Mean RTT','RTT p50','RTT p95']):
        r=report['extended']['metrics'][metric]
        ax.axhline(0,color='0.3',linewidth=.8)
        ax.plot(range(1,21),r['differences'],color=COLORS[0],marker='o',markersize=3,linewidth=.8)
        ax.axhline(r['difference'],color=COLORS[1],linestyle='--',linewidth=.9)
        ax.set(xlabel='Paired block [1]',ylabel='XDP − Kernel [ms]',title=label,xticks=[1,5,10,15,20])
    save(fig,root/'figures/urllc_paired_block_differences')


if __name__=='__main__':
    import argparse
    from pathlib import Path
    from c8_thesis_plots import analyze, ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'.work/c8-campaign/ieee-rr')
    analyze(parser.parse_args().root.resolve(),ieee=True)
