"""Independent small-enumeration checks for paired C8 inference."""
from pathlib import Path
import sys
from itertools import product
import numpy as np
import pytest
from scipy import stats

sys.path.insert(0,str(Path(__file__).parents[1]))
sys.path.insert(0,str(Path(__file__).parents[2]/'backend'))
from c8_ieee_stats import exact_wilcoxon, contrast, holm, paired_arrays
from c8_run_xdp_ieee import balanced_order


def test_exact_signed_ranks_match_enumeration_with_ties_and_zeros():
    d=np.array([-3.,-2.,-2.,0.,1.,3.]);d=d[d!=0]
    ranks=stats.rankdata(abs(d));w=ranks[d>0].sum()
    distribution=np.array([np.dot(signs,ranks) for signs in product([0,1],repeat=len(d))])
    r=exact_wilcoxon(d)
    assert r['p_less']==np.mean(distribution<=w)
    assert r['p_two_sided']==min(1,2*min(np.mean(distribution<=w),np.mean(distribution>=w)))


def test_exact_signed_ranks_match_scipy_without_ties():
    d=np.array([-1,-2,3,-4,-5,6,-7,-8,-9,10.])
    result=exact_wilcoxon(d)
    assert result['p_less']==stats.wilcoxon(d,alternative='less',method='exact').pvalue
    assert result['p_two_sided']==stats.wilcoxon(d,method='exact').pvalue


def test_bca_and_t_direction_reproducible_on_paired_blocks():
    a=np.arange(20.)+10;b=a+np.linspace(-3,-1,20)
    r=contrast(a,b);repeat=contrast(a,b)
    assert r==repeat
    assert r['bca']['99'][1]<0 and r['t']['p_less']<.01
    assert r['cohen_dz']==pytest.approx(np.mean(b-a)/np.std(b-a,ddof=1))
    expected=stats.bootstrap((b-a,),np.mean,n_resamples=10000,rng=np.random.default_rng(42017),method='BCa')
    assert r['bca']['95']==pytest.approx([expected.confidence_interval.low,expected.confidence_interval.high])


def test_degenerate_zero_differences_do_not_claim_superiority():
    r=contrast(np.arange(20.),np.arange(20.))
    assert r['bca']=={'95':None,'99':None}
    assert r['cohen_dz'] is None and r['wilcoxon']['p_less']==r['t']['p_less']==1


def test_holm_and_fixed_balanced_design():
    assert holm({'a':.001,'b':.02,'c':.03})=={'a':.003,'b':.04,'c':.04}
    order=balanced_order()
    assert order==balanced_order() and order.count(['kernel','xdp'])==order.count(['xdp','kernel'])==10


def test_missing_pair_cannot_be_silently_discarded():
    with pytest.raises(ValueError):paired_arrays([{'block':0,'mode':'kernel','x':1}], 'x')


def test_cdf_resampling_uses_blocks_and_ack_denominators():
    from c8_ieee_plots import cdf_band
    samples=[np.array([1.,2.]),np.array([10.])]
    indices=np.array([[0,0],[0,1],[1,0],[1,1]])
    grid=np.array([0.,2.,10.])
    lower,upper=cdf_band(samples,grid,indices)
    expected=np.array([[0,1,1],[0,2/3,1],[0,2/3,1],[0,0,1]])
    assert lower==pytest.approx(np.quantile(expected,.025,axis=0))
    assert upper==pytest.approx(np.quantile(expected,.975,axis=0))
