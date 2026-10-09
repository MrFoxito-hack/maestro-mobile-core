"""Exact conditional signed-rank distribution with Pratt zero handling.

Zeros participate in ranking but not sign assignment. Ties receive midranks.
The integer dynamic program counts all sign assignments of the nonzero pairs.
"""
import numpy as np
from scipy import stats


def exact_pratt(differences):
    d = np.round(np.asarray(differences, dtype=float), 12)
    if d.ndim != 1 or not np.all(np.isfinite(d)):
        raise ValueError('Expected finite paired differences')
    all_ranks = np.rint(2 * stats.rankdata(np.abs(d), method='average')).astype(int)
    nonzero = d != 0
    ranks = all_ranks[nonzero]
    counts = [1]
    for rank in ranks:
        nxt = [0] * (len(counts) + int(rank))
        for i, count in enumerate(counts):
            nxt[i] += count
            nxt[i + rank] += count
        counts = nxt
    observed = int(all_ranks[d > 0].sum())
    total = 2 ** int(nonzero.sum())
    lower = sum(counts[:observed + 1]) / total
    upper = sum(counts[observed:]) / total
    return {'statistic_wplus': observed / 2, 'p_less': lower,
            'p_two_sided': min(1., 2 * min(lower, upper)),
            'nonzero_pairs': int(nonzero.sum()), 'zero_pairs': int((~nonzero).sum()),
            'method': 'exact conditional sign DP; Pratt zeros; midrank ties; rounded to 12 decimals'}


def analyze_rows(rows):
    # Reuse the accepted paired-t, BCa and Holm implementation, while making
    # the requested zero convention explicit only for this new campaign.
    import c8_ieee_stats as base
    previous = base.exact_wilcoxon
    try:
        base.exact_wilcoxon = exact_pratt
        return base.analyze_rows(rows)
    finally:
        base.exact_wilcoxon = previous
