"""Reproducible analysis of complete_benchmark.py artifacts (no SSH needed)."""
import argparse
import csv
import ipaddress
import json
import math
from pathlib import Path
import re
import statistics as st
import struct


def percentile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def pcap_packets(path):
    raw = path.read_bytes()
    endian = '<' if raw[:4] == b'\xd4\xc3\xb2\xa1' else '>'
    assert raw[:4] in [b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4']
    linktype = struct.unpack(endian + 'I', raw[20:24])[0]
    assert linktype in (12, 101, 228), linktype
    offset, packets = 24, []
    while offset < len(raw):
        sec, usec, length, original = struct.unpack(endian + 'IIII', raw[offset:offset + 16])
        data = raw[offset + 16:offset + 16 + length]
        offset += 16 + length
        version = data[0] >> 4
        packet = {'timestamp': sec + usec / 1e6, 'length': original, 'ip_version': version}
        if version == 4:
            ihl = (data[0] & 15) * 4
            packet.update(src=str(ipaddress.ip_address(data[12:16])),
                          dst=str(ipaddress.ip_address(data[16:20])), protocol=data[9])
            if data[9] == 1:
                packet.update(icmp_type=data[ihl], icmp_code=data[ihl + 1])
        packets.append(packet)
    return packets


def receiver_mbps(result, protocol, direction):
    if protocol == 'tcp':
        return result['end']['sum_received']['bits_per_second'] / 1e6, 'end.sum_received'
    # iperf 3.9 end.sum combines sender bytes/rate with receiver loss/jitter.
    if direction == 'ul':
        text = result['server_output_text']
        lines = [line for line in text.splitlines() if line.rstrip().endswith('receiver')]
        assert len(lines) == 1, lines
        rate, unit = re.search(r'([\d.]+) ([KMG]?)bits/sec', lines[0]).groups()
        return float(rate) * {'': 1e-6, 'K': .001, 'M': 1, 'G': 1000}[unit], 'server_output_text receiver (rounded by iperf)'
    intervals = [i['sum'] for i in result['intervals'] if not i['sum'].get('omitted')]
    assert all(i['sender'] is False for i in intervals)
    return sum(i['bytes'] for i in intervals) * 8 / sum(i['seconds'] for i in intervals) / 1e6, 'receiver intervals bytes / seconds'


def analyze(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    rows = []
    for name in manifest['trials']:
        repeat, mode, protocol, direction = name.split('-')
        read = lambda suffix: json.loads((directory / (name + suffix)).read_text())
        result, before, after = read('.json'), read('-before.json'), read('-after.json')
        assert not result.get('error')
        assert read('.json.execution.json')['exit_code'] == 0
        assert before['upfd_pid'] == after['upfd_pid']
        link = lambda snap: next(i['stats64'] for i in snap['links'] if i['ifname'] == 'ogstun')
        bpf = {k: after['bpf'][k]['packets'] - before['bpf'][k]['packets'] for k in before['bpf']}
        seconds = after['time'] - before['time']
        cpu = [b - a for a, b in zip(before['cpu'][:8], after['cpu'][:8])]
        program_a, program_b = before['bpf_program'], after['bpf_program']
        executions = program_b['run_cnt'] - program_a['run_cnt']
        runtime = program_b['run_time_ns'] - program_a['run_time_ns']
        ping = (directory / (name + '-ping.txt')).read_text()
        samples = [(int(seq), float(rtt)) for seq, rtt in re.findall(r'icmp_seq=(\d+).*?time=([\d.]+) ms', ping)]
        rtts = [rtt for _, rtt in samples]
        jitter = [abs(b[1] - a[1]) for a, b in zip(samples, samples[1:]) if b[0] == a[0] + 1]
        transmitted, received = map(int, re.search(r'(\d+) packets transmitted, (\d+) received', ping).groups())
        assert len(rtts) == received
        mbps, source = receiver_mbps(result, protocol, direction)
        packets = pcap_packets(directory / (name + '-ogstun.pcap'))
        capture = (directory / (name + '-capture.txt')).read_text()
        assert int(re.search(r'(\d+) packets? captured', capture).group(1)) == len(packets)
        capture_drops = int(re.search(r'(\d+) packets dropped by kernel', capture).group(1))
        row = dict(trial=name, repeat=int(repeat), mode=mode, protocol=protocol, direction=direction,
                   receiver_mbps=mbps, receiver_source=source, snapshot_seconds=seconds,
                   cpu_busy_pct=(sum(cpu) - cpu[3] - cpu[4]) / sum(cpu) * 100,
                   upfd_cpu_pct=(after['upfd_ticks'] - before['upfd_ticks']) / before['clock_ticks'] / seconds * 100,
                   softirq_pct=cpu[6] / sum(cpu) * 100,
                   upfd_rss_kb=after['upfd_rss_kb'], bpf_invocations=executions,
                   bpf_runtime_ns=runtime, bpf_ns_per_invocation=runtime / executions if executions else None,
                   bpf_cpu_pct=runtime / 1e9 / seconds * 100,
                   ogstun_rx=link(after)['rx']['packets'] - link(before)['rx']['packets'],
                   ogstun_tx=link(after)['tx']['packets'] - link(before)['tx']['packets'],
                   capture_packets=len(packets), capture_drops=capture_drops,
                   capture_tcp_udp=sum(p.get('protocol') in (6, 17) for p in packets),
                   capture_icmp_errors=sum(p.get('protocol') == 1 and p.get('icmp_type') not in (0, 8) for p in packets),
                   bpf_packets=bpf, ping_sent=transmitted, ping_received=received,
                   ping_loss_pct=(transmitted - received) / transmitted * 100,
                   rtt_mean_ms=st.mean(rtts), rtt_p99_ms=percentile(rtts, .99),
                   rtt_std_ms=st.pstdev(rtts), successive_rtt_jitter_ms=st.mean(jitter),
                   rtts_ms=rtts, rtt_differences_ms=jitter)
        if mode == 'xdp':
            row['captured_packet_details'] = packets
        if protocol == 'udp':
            row.update(udp_jitter_ms=result['end']['sum']['jitter_ms'],
                       udp_loss_pct=result['end']['sum']['lost_percent'])
        rows.append(row)
    groups = []
    for protocol in ['tcp', 'udp']:
        for direction in ['ul', 'dl']:
            pair = {}
            for mode in ['legacy', 'xdp']:
                items = [r for r in rows if (r['protocol'], r['direction'], r['mode']) == (protocol, direction, mode)]
                if not items:
                    continue
                rtts = [v for r in items for v in r['rtts_ms']]
                jitter = [v for r in items for v in r['rtt_differences_ms']]
                group = dict(protocol=protocol, direction=direction, mode=mode, n=len(items),
                             throughput_mean_mbps=st.mean(r['receiver_mbps'] for r in items),
                             throughput_sd_mbps=st.stdev(r['receiver_mbps'] for r in items) if len(items) > 1 else 0,
                             rtt_mean_ms=st.mean(rtts), rtt_p99_ms=percentile(rtts, .99), rtt_std_ms=st.pstdev(rtts),
                             successive_rtt_jitter_ms=st.mean(jitter),
                             ping_sent=sum(r['ping_sent'] for r in items), ping_received=len(rtts),
                             ping_loss_pct=(1 - len(rtts) / sum(r['ping_sent'] for r in items)) * 100)
                for metric in ['cpu_busy_pct', 'upfd_cpu_pct', 'softirq_pct', 'bpf_cpu_pct', 'upfd_rss_kb']:
                    group[metric] = st.mean(r[metric] for r in items)
                group['bpf_ns_per_invocation'] = sum(r['bpf_runtime_ns'] for r in items) / sum(r['bpf_invocations'] for r in items)
                if protocol == 'udp':
                    group['udp_jitter_ms'] = st.mean(r['udp_jitter_ms'] for r in items)
                    group['udp_loss_pct'] = st.mean(r['udp_loss_pct'] for r in items)
                groups.append(group)
                pair[mode] = group
            if len(pair) == 2:
                pair['xdp']['improvement_pct'] = (pair['xdp']['throughput_mean_mbps'] / pair['legacy']['throughput_mean_mbps'] - 1) * 100
    output = {'manifest': manifest, 'groups': groups, 'trials': rows}
    (directory / 'analysis.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    simple = [{k: v for k, v in row.items() if not isinstance(v, (list, dict))} for row in rows]
    fields = list(dict.fromkeys(k for row in simple for k in row))
    with (directory / 'trials.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(simple)
    print(json.dumps(groups, indent=2))
    print('XDP bypass:', [(r['trial'], r['ogstun_rx'], r['ogstun_tx'], r['capture_packets']) for r in rows if r['mode'] == 'xdp'])
    return output


def plots(directory, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    groups = output['groups']
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.3))
    labels = ['TCP UL', 'TCP DL', 'UDP UL', 'UDP DL']
    colors = {'legacy': '#58677c', 'xdp': '#167d9a'}
    for mode, shift in [('legacy', -.19), ('xdp', .19)]:
        selected = [g for g in groups if g['mode'] == mode]
        positions = [i + shift for i in range(4)]
        axes[0].bar(positions, [g['throughput_mean_mbps'] for g in selected], .36,
                    yerr=[g['throughput_sd_mbps'] for g in selected], capsize=4, color=colors[mode], label='XDP' if mode == 'xdp' else 'Legacy')
        axes[1].bar(positions, [g['rtt_mean_ms'] for g in selected], .36, color=colors[mode], label='XDP' if mode == 'xdp' else 'Legacy')
    for ax in axes:
        ax.set_xticks(range(4), labels)
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
        ax.legend()
    axes[0].set_ylabel('Throughput recibido (Mbps)')
    axes[0].set_title('Media ± desviación estándar; n = 3')
    axes[1].set_ylabel('RTT medio de ping (ms)')
    axes[1].set_title('Sondeo iniciado con la carga')
    figure.suptitle('UPF Open5GS: Legacy y XDP genérico · UDP ofrecido: 10 Mbps')
    figure.tight_layout()
    figure.savefig(directory / 'comparacion.png', dpi=180)
    figure.savefig(directory / 'comparacion.pdf')
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--plots', action='store_true')
    args = parser.parse_args()
    output = analyze(args.directory)
    if args.plots:
        plots(args.directory, output)
