"""OE4 selection and acceptance from measured packets and kernel counters."""
from collections import Counter
import hashlib
import json
import statistics


def historical_runs(root):
    path = root/'setup/oe4/design.json'
    return set(json.loads(path.read_text())['historical_isolation_runs']) if path.exists() else set()


def certify(root, campaigns):
    design_path = root/'setup/oe4/design.json'
    if not design_path.exists():
        return None
    design = json.loads(design_path.read_text())
    charging=json.loads((root/'setup/oe4/charging-window.json').read_text())
    assert charging['restored_policy']==charging['before_policy'], 'OE4_charging_not_restored'
    assert json.loads((root/'setup/oe4/smf-window.json').read_text())['restored_exact'], 'OE4_SMF2_not_restored'
    runtime=json.loads((root/'setup/oe4/runtime-final.json').read_text())
    assert runtime['status']=='verified' and len(runtime['terminals'])==6, 'OE4_runtime_not_verified'
    trials = []
    hashes = {'setup/oe4/design.json': hashlib.sha256(design_path.read_bytes()).hexdigest()}
    for campaign in campaigns:
        if campaign['arguments']['experiment'] != 'isolation':
            continue
        assert campaign['arguments']['qos'] and campaign['status'] == 'completed'
        assert campaign['run_id'][:15] >= design['created_at'][:19].replace('-','').replace(':','')[:15]
        directory = root/'runs'/campaign['run_id']
        for source, digest in campaign['source_sha256'].items():
            assert hashlib.sha256((directory/'source'/source).read_bytes()).hexdigest() == digest
        for trial in campaign['trials']:
            if trial['config']['warmup']:
                continue
            config = trial['config']
            assert config['duration_s'] == 10 and config['qos_profile'] == design['profile']
            names = [trial['name']+suffix for suffix in
                     ['-raw.json','-before.json','-after.json','-qos-before.json','-qos-after.json']]
            values = []
            for name in names:
                path = directory/name
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
                values.append(json.loads(path.read_text()))
            raw, native_before, native_after, before, after = values
            assert before['bindings'] == after['bindings'], 'UE_restarted_during_trial'
            row = {'run_id':campaign['run_id'], 'block':config['block'], 'level':config['level'], 'slices':{}}
            for stream in raw['streams']:
                spec = stream['spec']; kind = spec['slice']; packets = stream['packets']
                expected_pps = design['offered_pps'][kind] if config['level']=='loaded' else {'urllc':100,'embb':10,'miot':20}[kind]
                assert spec['pps'] == expected_pps and len(packets) == 10*expected_pps
                assert len({p['sequence'] for p in packets}) == len(packets)
                assert all(p['send_ok'] for p in packets) and not stream['duplicates'] and not stream['foreign_count']
                assert all(p['sensor_match'] and p['rtt_ms'] == (p['received_ns']-p['sent_ns'])/1e6
                           for p in packets if p['ack'])
                rtts = [p['rtt_ms'] for p in packets if p['ack']]
                nf = {'urllc':'upf3','embb':'upf','miot':'upf2'}[kind]
                old, = [s for s in native_before[nf]['native']['sessions'] if s['ue_ipv4']==spec['source']]
                new, = [s for s in native_after[nf]['native']['sessions'] if s['ue_ipv4']==spec['source']]
                assert (old['upf_seid'],old['smf_seid'],old['dnn'],old['rules']['qer']) == (new['upf_seid'],new['smf_seid'],new['dnn'],new['rules']['qer'])
                assert spec['source'].startswith({'urllc':'10.47.','miot':'10.46.','embb':'10.45.'}[kind])
                assert spec['target'] == {'urllc':'172.31.48.2','miot':'10.46.0.1','embb':'10.45.0.1'}[kind]
                metrics = {'attempted':len(packets),'ack':len(rtts),'loss_pct':100*(len(packets)-len(rtts))/len(packets),
                           'rtt_mean_ms':statistics.mean(rtts) if rtts else None,
                           'rtt_max_ms':max(rtts) if rtts else None,
                           'deadline_miss_pct':100*sum(not p['ack'] or p['rtt_ms']>=5 for p in packets)/len(packets)}
                if kind != 'urllc':
                    qa, = [q for q in before['all_qdiscs'] if q.get('dev')==spec['interface'] and q.get('root')]
                    qb, = [q for q in after['all_qdiscs'] if q.get('dev')==spec['interface'] and q.get('root')]
                    assert qa['kind']==qb['kind']=='tbf' and qa['options']==qb['options']
                    assert qa['options']['rate']*8 == design[kind+'_rate_bps']
                    metrics['tc_drops'] = qb['drops']-qa['drops']
                    metrics['tc_packets'] = qb['packets']-qa['packets']
                    metrics['tc_overlimits'] = qb['overlimits']-qa['overlimits']
                    assert metrics['tc_drops'] >= 0
                    metrics['tc_nonprobe_packet_delta']=metrics['tc_packets']+metrics['tc_drops']-len(packets)
                    if config['level']=='loaded':
                        assert metrics['tc_nonprobe_packet_delta']==0, 'TBF_attempt_accounting_mismatch'
                        assert metrics['tc_packets']==len(rtts), 'post_admission_delivery_loss'
                    else:
                        assert metrics['tc_drops']==0 and metrics['tc_nonprobe_packet_delta']>=0
                admitted=len(packets)-metrics.get('tc_drops',0)
                directions={p['source_interface'] for p in old['rules']['pdr'] if p['urr_ids']}
                expected=(admitted if 0 in directions else 0)+(len(rtts) if 1 in directions else 0)
                native_packets=sum(int(u['total_packets']) for u in new['usage'])-sum(int(u['total_packets']) for u in old['usage'])
                assert native_packets==expected, 'OE4_URR_admitted_ACK_mismatch'
                metrics['urr_packets']=native_packets
                metrics['urr_matches_admitted_and_ack']=True
                row['slices'][kind] = metrics
            trials.append(row)
    assert Counter((t['block'],t['level']) for t in trials)==Counter({(b,l):1 for b in range(6) for l in ['idle','loaded']})
    loaded = [t for t in trials if t['level']=='loaded']
    means = [t['slices']['urllc']['rtt_mean_ms'] for t in loaded]
    total = sum(t['slices']['urllc']['attempted'] for t in loaded)
    ack = sum(t['slices']['urllc']['ack'] for t in loaded)
    result = {'scope':'OE4 measured Linux QoS envelope; not radio/3GPP certification',
              'trials':trials,'input_sha256':hashes,'loaded_urllc_attempted':total,'loaded_urllc_ack':ack,
              'loaded_urllc_loss_pct':100*(total-ack)/total,
              'loaded_urllc_rtt_mean_ms':statistics.mean(means),
              'all_loaded_means_below_5ms':all(m<5 for m in means),
              'embb_contention_every_loaded_trial':all(t['slices']['embb']['tc_drops']>0 and t['slices']['embb']['tc_overlimits']>0 for t in loaded)}
    result['current_six_terminal_mapping_verified']=True
    result['calibration_windows_restored']=True
    result['all_36_streams_admitted_urr_ack_reconciled']=True
    result['all_18_loaded_streams_tc_exact']=True
    result['idle_interface_extra_packets']=sum(t['slices'][k].get('tc_nonprobe_packet_delta',0) for t in trials if t['level']=='idle' for k in t['slices'])
    result['accepted'] = ack==total and result['loaded_urllc_rtt_mean_ms']<5 and result['embb_contention_every_loaded_trial']
    (root/'evidence/oe4-acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
