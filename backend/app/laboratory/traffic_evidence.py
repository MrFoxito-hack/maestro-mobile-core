"""Receiver accounting for the admitted iperf3 UDP reverse workload."""
import math


def received_udp_bytes(document):
    if document.get('error'):
        raise ValueError('competing_traffic_failed')
    start = document.get('start', {}).get('test_start', {})
    if start.get('protocol') != 'UDP' or start.get('reverse') != 1:
        raise ValueError('competing_traffic_profile_unverified')
    # iperf 3.9 end.sum can report sender bytes even with sender=false. Use
    # the receiving client's disjoint interval counters, never that summary.
    total, previous = 0, 0.0
    intervals = document.get('intervals', [])
    if not intervals:
        raise ValueError('competing_receiver_intervals_missing')
    for interval in intervals:
        sample = interval.get('sum', {})
        begin, end, count = sample.get('start'), sample.get('end'), sample.get('bytes')
        if (sample.get('sender') is not False or sample.get('omitted') is not False
                or type(count) is not int or count < 0
                or type(begin) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(begin) or not math.isfinite(end)
                or abs(begin - previous) > .01 or end <= begin):
            raise ValueError('competing_receiver_interval_invalid')
        total += count
        previous = end
    if not 9.5 <= previous <= 11 or total <= 0:
        raise ValueError('competing_receiver_duration_or_bytes_invalid')
    return total
