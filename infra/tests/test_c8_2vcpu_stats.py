"""Cross-check exact Pratt DP against independent exhaustive sign enumeration."""
import itertools
import sys
from pathlib import Path
import numpy as np
import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c8_2vcpu_stats import exact_pratt


@pytest.mark.parametrize('values', [
    [0, 0, 0], [0, -1, 1, -2, 2], [0, 0, -1, -1, 3],
    [-1, -2, -3, 4, 5], [1, 1, 2, -2, -3, 0],
])
def test_pratt_against_exhaustive_enumeration(values):
    d = np.array(values, dtype=float)
    ranks = stats.rankdata(abs(d))
    observed = sum(ranks[d > 0])
    active = ranks[d != 0]
    distribution = [sum(r * s for r, s in zip(active, signs))
                    for signs in itertools.product([0, 1], repeat=len(active))]
    lower = np.mean(np.array(distribution) <= observed)
    upper = np.mean(np.array(distribution) >= observed)
    result = exact_pratt(values)
    assert result['p_less'] == lower
    assert result['p_two_sided'] == min(1., 2 * min(lower, upper))


def test_matches_scipy_when_no_zeros_or_ties():
    d = [-1, -2, 3, -4, 5, -6]
    assert exact_pratt(d)['p_less'] == stats.wilcoxon(
        d, zero_method='pratt', alternative='less', method='exact').pvalue


def test_rejects_missing_difference():
    with pytest.raises(ValueError):
        exact_pratt([1, float('nan')])
