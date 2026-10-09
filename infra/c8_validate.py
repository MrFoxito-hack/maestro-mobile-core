"""C8 artifact acceptance only; no network access and no C0-C7 revalidation."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def validate(root, profile='baseline'):
    from c8_thesis_plots import summarize
    extended=profile=='ieee'
    blocks,duration=(20,15) if extended else (6,10)
    expected_counts={'urllc':40} if extended else {'miot':24,'isolation':12,'urllc':12}
    if extended:
        protocol_path=root/'setup/preregistered-design.json'
        assert hashlib.sha256(protocol_path.read_bytes()).hexdigest()==(root/'setup/preregistered-design.sha256').read_text().strip()
        protocol=json.loads(protocol_path.read_text())
        assert (protocol['blocks'],protocol['duration_s'],protocol['formal_packets'])==(20,15,60000)
        acquisition=json.loads((root/'setup/acquisition.json').read_text())
        assert acquisition['status']=='completed' and len(acquisition['completed'])==40
        assert protocol['created_at']<acquisition['started_at']
    campaigns = [json.loads(p.read_text()) for p in sorted((root/'runs').glob('*/campaign.json'))]
    from c8_oe4 import historical_runs, certify
    historical = historical_runs(root)
    exclusions=json.loads((root/'setup/exclusions.json').read_text())['runs']
    formal = [c for c in campaigns if c['arguments']['experiment'] != 'pilot' and c['run_id'] not in exclusions and c['run_id'] not in historical]
    if extended:assert len(formal)==40 and not exclusions and len(campaigns)==40
    assert all(c['status'] == 'completed' for c in formal), 'incomplete_formal_campaign'
    rows, configs, warmups, identities, qers = [], [], [], set(), []
    both_identities=set();both_qers=[]
    for c in formal:
        if extended:
            assert c['arguments']['seed']==42017+c['arguments']['block_start']
            for name,digest in c['source_sha256'].items():
                assert hashlib.sha256((root/'runs'/c['run_id']/'source'/name).read_bytes()).hexdigest()==digest
                assert digest==acquisition['source_sha256'][name]
        for trial in c['trials']:
            directory = root/'runs'/c['run_id']; name = trial['name']
            raw = json.loads((directory/(name+'-raw.json')).read_text())
            config = raw['configuration']
            assert config==trial['config']
            if config['warmup']:
                if extended:assert config['duration_s']==2
                warmups.append(raw)
                continue
            before = json.loads((directory/(name+'-before.json')).read_text())
            after = json.loads((directory/(name+'-after.json')).read_text())
            for stream in raw['streams']:
                packets=stream['packets']
                assert [p['sequence'] for p in packets]==list(range(int(config['duration_s']*stream['spec']['pps'])))
                assert all(p['send_ok'] and p['sensor_match'] and p['rtt_ms']>=0 for p in packets if p['ack'])
                if extended:
                    assert not stream['duplicates'] and stream['foreign_count']==0
                    assert all(p['rtt_ms']==(p['received_ns']-p['sent_ns'])/1e6 for p in packets if p['ack'])
                if config['experiment']=='miot':
                    assert Counter(p['sensor_id'] for p in packets)=={sensor:2 for sensor in range(1,stream['spec']['sensors']+1)}
            measured, _ = summarize(raw, before, after)
            rows.extend(measured); configs.append(config)
            if config['experiment'] == 'urllc':
                assert config['duration_s'] == duration
                spec, = config['streams']
                assert (spec['target'], spec['port'], spec['pps'], spec['payload_bytes']) == ('172.31.48.2',8765,100,64)
                for snapshot in [before['upf3'], after['upf3']]:
                    session, = [s for s in snapshot['native']['sessions'] if s['ue_ipv4'] == spec['source']]
                    identities.add((snapshot['native']['pid'], snapshot['native']['generation'],
                                    session['upf_seid'], session['smf_seid'], session['ue_ipv4'], spec['interface']))
                    qers.append(session['rules']['qer'])
                    if extended:
                        sessions=sorted(snapshot['native']['sessions'],key=lambda s:s['ue_ipv4'])
                        assert len(sessions)==2 and all(s['ue_ipv4'].startswith('10.47.0.') for s in sessions)
                        both_identities.add(tuple((s['ue_ipv4'],s['upf_seid'],s['smf_seid']) for s in sessions))
                        both_qers.append([s['rules']['qer'] for s in sessions])
    counts = Counter(c['experiment'] for c in configs)
    assert counts == expected_counts, dict(counts)
    if extended:
        assert len(warmups)==40 and sum(r['attempted'] for r in rows)==60000
        assert len(both_identities)==1 and all(q==both_qers[0] for q in both_qers)
    assert len(identities) == 1, 'URLLC_identity_changed_between_arms'
    assert all(q == qers[0] for q in qers), 'URLLC_QER_changed_between_arms'
    relevant = [r for r in rows if r['experiment'] in ('urllc','miot')]
    assert all(r['identity_stable'] and r['qer_unchanged'] and r['urr_matches_endpoint'] for r in relevant)
    assert all(0 <= r['received'] <= r['sent'] <= r['attempted'] and
               0 <= r['delivery_pct'] <= 100 and 0 <= r['deadline_miss_pct'] <= 100 for r in rows)
    assert all(0 <= r['rtt_p50_ms'] <= r['rtt_p95_ms'] <= r['rtt_p99_ms'] and
               0 <= r['jitter_mean_ms'] <= r['jitter_max_ms'] for r in rows if r['received'] > 1)
    assert Counter((r['block'],r['mode']) for r in rows if r['experiment']=='urllc') == Counter({(b,m):1 for b in range(blocks) for m in ['kernel','xdp']})
    order = json.loads((root/'setup/xdp-order.json').read_text())
    observed = [[c['mode'] for c in configs if c['experiment']=='urllc' and c['block']==b] for b in range(blocks)]
    assert observed == order['blocks'] and order['seed']==42017, 'unbalanced_or_changed_order'
    assert Counter(tuple(x) for x in observed)=={('kernel','xdp'):blocks//2,('xdp','kernel'):blocks//2}
    if extended:assert observed==protocol['order']
    window = json.loads((root/'setup/xdp-window.json').read_text())
    assert window['mode']=='restore' and window['restored'] and len(window['restored_pdu']['tunnels'])==2
    assert [c['mode'] for c in window['pdu_checks']]==[mode for block in observed for mode in block]
    assert all(set(c['nas'])=={'002','005'} and all(
        s['state']=='PS-ACTIVE' and s['address']==window['pdu']['nas'][suffix]['address']
        for suffix,s in c['nas'].items()) for c in window['pdu_checks'])
    # Controllers can span consecutive XDP runs; deduplicate archived snapshots.
    controls = {}
    for p in (root/'setup').glob('controller-*.json'):
        c = json.loads(p.read_text())
        if c.get('scope')=='c8_experiment' and (c.get('pid'),c.get('instance'))==(window['identities'][0]['pid'],window['identities'][0]['instance']):
            controls[c['started_monotonic']] = c
    assert controls and all(c.get('finished') and c.get('gate_closed') and 'error' not in c for c in controls.values())
    # ABI usage[path native/XDP][direction UL/DL] stores (packets, inner-IP bytes).
    measured_address = next(iter(identities))[4]
    epochs=[e for c in controls.values() for e in c['epochs']]
    fast_ul = sum(e['after'][measured_address][4]-e['before'][measured_address][4] for e in epochs)
    fast_dl = sum(e['after'][measured_address][6]-e['before'][measured_address][6] for e in epochs)
    bpf_delta={name:window['counters_after'][name]-window['counters_before'][name] for name in ['UL_OK','DL_OK']}
    for name,index in [('UL_OK',4),('DL_OK',6)]:
        assert bpf_delta[name] == sum(e['after'][address][index]-e['before'][address][index]
            for e in epochs for address in e['after']), ('BPF_controller_mismatch',name)
    xdp_rows = [r for r in rows if r['experiment']=='urllc' and r['mode']=='xdp']
    xdp_warmup = [r for r in warmups if r['configuration']['experiment']=='urllc' and r['configuration']['mode']=='xdp']
    sent = sum(r['sent'] for r in xdp_rows)+sum(sum(p['send_ok'] for p in r['streams'][0]['packets']) for r in xdp_warmup)
    received = sum(r['received'] for r in xdp_rows)+sum(sum(p['ack'] for p in r['streams'][0]['packets']) for r in xdp_warmup)
    assert 0<fast_ul<=sent and 0<fast_dl<=received, ('XDP_path_accounting_mismatch',fast_ul,fast_dl,sent,received)
    if not extended:
        assert json.loads((root/'setup/smf-window.json').read_text())['restored_exact']
        charging=json.loads((root/'setup/charging-window.json').read_text())
        assert charging['restored_policy']==charging['before_policy'], 'charging_policy_not_restored'
    else:
        inference=json.loads((root/'evidence/statistics_ieee.json').read_text())
        assert all(m['n_pairs']==20 for m in inference['extended']['metrics'].values())
        assert inference['extended']['bootstrap_resamples']==10000
        assert inference['preregistration_sha256']==hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in inference['input_sha256'].items())
        for name,digest in acquisition['source_sha256'].items():
            assert hashlib.sha256((root/'setup/executed-source'/name).read_bytes()).hexdigest()==digest
        frozen=json.loads((root/'setup/frozen-source-start.json').read_text())
        assert len(frozen)==785 and all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    stems=['urllc_rtt_cdf','urllc_cpu_per_packet'] if extended else ['urllc_rtt_cdf','slice_isolation','miot_scaling','qoe_abba_mos']
    for stem in stems:
        for extension in ['pdf','svg','png']:assert (root/'figures'/f'{stem}.{extension}').stat().st_size>1000
    result = {'accepted':True,'scope':'C8 only; frozen C0-C7 accepted as antecedents',
              'profile':profile,
              'created_at':datetime.now(timezone.utc).isoformat(),'formal_trials':dict(counts),
              'formal_streams':len(rows),'formal_packets':sum(r['attempted'] for r in rows),
              'urllc_identity':list(next(iter(identities))),'qer_unchanged_between_arms':True,
              'balanced_order':observed,'xdp_packets_including_warmup':{'ul':fast_ul,'dl':fast_dl},
              'bpf_counter_deltas':bpf_delta,'both_pdu_checks':len(window['pdu_checks']),
              'xdp_endpoint_including_warmup':{'ul':sent,'dl':received},
              'xdp_fast_share_pct':{'ul':100*fast_ul/sent,'dl':100*fast_dl/received},
              'excluded_attempts':exclusions,'generation_epochs':len(epochs),
              'xdp_controller_windows':len(controls),'service_and_pdu_restored':True,
              'smf2_config_restored_exact':True if not extended else None,'charging_policy_restored':True if not extended else None,
              'smf2_charging_window_used':not extended,
              'hypothesis_acceptance_is_not_a_closure_requirement':True}
    if extended:
        result['primary_joint_criterion_met']=inference['extended']['primary_joint_criterion_met']
        result['frozen_source_files_unchanged']=785
        if 'scheduler' in protocol:
            from c8_oe3 import validate_rt_evidence
            result['realtime_evidence']=validate_rt_evidence(root,protocol,formal)
    if not extended and historical:
        oe4 = certify(root, formal)
        result['oe4_performance_accepted'] = oe4['accepted']
        result['historical_isolation_runs_retained'] = sorted(historical)
    path=root/'evidence/certification-status.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'.work/c8-campaign')
    parser.add_argument('--profile',choices=['baseline','ieee'],default='baseline')
    args=parser.parse_args();validate(args.root,args.profile)
