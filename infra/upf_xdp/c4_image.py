"""Compile native observations into INACTIVE C4 policy proposals.

No bpftool mutation or activation. The proposal is deliberately insufficient
for forwarding: native QER takeover, URR import, complete PFCP eligibility and
observed SUPI/PDU correlation still require a verified native contract.
"""
import ipaddress
import struct

TARGETS = {'10.47.0.2': '002', '10.47.0.3': '005'}
MAX_RATE = 10**12


def integer(value, maximum=(1 << 64) - 1, minimum=0):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid unsigned policy integer')
    return value


def session_image(registry, session):
    ue = str(ipaddress.IPv4Address(session['ue']))
    if ue not in TARGETS or session['dnn'] != '5g-plus':
        raise ValueError('Session outside the two configured URLLC vehicles')
    pdrs, qers, urrs = session['pdrs'], session['qers'], session['urrs']
    if len(pdrs) != 2 or len(qers) != 1 or len(urrs) != 1:
        raise ValueError('Candidate supports exactly two PDRs, one QER and one URR')
    if {integer(p['source'], 1) for p in pdrs} != {0, 1}:
        raise ValueError('Both ACCESS and CORE PDRs are required')
    ul, dl = sorted(pdrs, key=lambda p: p['source'])
    qer, urr = qers[0], urrs[0]
    qid = integer(qer['id'], (1 << 32) - 1, 1)
    uid = integer(urr['id'], (1 << 32) - 1, 1)
    qfi = integer(qer['qfi'], 63, 1)
    mask = 0
    for direction, pdr in enumerate((ul, dl)):
        if (integer(pdr['qer_id']) != qid or pdr['sdf'] is not False or
                integer(pdr['far_action']) not in (2, 512) or integer(pdr['qfi'], 63) not in (0, qfi)):
            raise ValueError('Unsupported PDR/FAR/QER association')
        ids = [integer(x) for x in pdr['urr_ids']]
        if ids not in ([], [uid]):
            raise ValueError('Unsupported PDR/URR association')
        if ids: mask |= 1 << direction
    if not mask:
        raise ValueError('URR is not associated with either direction')
    if qer['gbr'] != [0, 0] or any(type(v) is not int for v in qer['gbr']):
        raise ValueError('GBR requires the native path')
    rates = [integer(v, MAX_RATE, 12000) for v in qer['mbr']]
    gates = [integer(v, 1) for v in qer['gates']]
    if len(rates) != 2 or len(gates) != 2:
        raise ValueError('Two directional MBRs and gates are required')
    if ul['upf'] != '10.210.50.22':
        raise ValueError('Wrong UPF address')
    gnb = str(ipaddress.IPv4Address(dl['gnb']))
    ul_teid = integer(ul['teid'], (1 << 32) - 1, 1)
    dl_teid = integer(dl['dl_teid'], (1 << 32) - 1, 1)
    identity = [integer(registry['instance'], minimum=1), integer(session['seid'], minimum=1),
                integer(session.get('generation', registry['generation']), minimum=1), qid, uid]
    key = struct.pack('<QQQII', *identity)
    quota_active, expired = urr['quota_active'], urr['quota_expired']
    if type(quota_active) is not bool or type(expired) is not bool:
        raise ValueError('Unverified quota state')
    remaining = max(0, integer(urr['quota_limit']) - integer(urr['native_bytes'])) if quota_active else 0
    if expired: remaining = 0
    # Proposed burst: 100 ms, at least a 1500-byte IP packet. Empty budget and
    # zero lease prevent stale observation from granting an artificial refill.
    buckets = [item for rate in rates for item in (rate, max(12000, rate // 10), 0, 0, 0)]
    value = (struct.pack('<II', 0, 1) + struct.pack('<10Q', *buckets) + bytes(64) +
             struct.pack('<QQIIII', remaining, 0, int(quota_active or expired), mask,
                         gates[0] | gates[1] << 1, 0) + struct.pack('<Q', 0))
    assert len(key) == 32 and len(value) == 192
    return {'ue': ue, 'configured_imsi_suffix': TARGETS[ue], 'identity_verified': False,
            'identity': identity, 'ul_teid': ul_teid, 'dl_teid': dl_teid, 'qfi': qfi,
            'upf': ul['upf'], 'gnb': gnb, 'policy_key_hex': key.hex(),
            'policy_value_hex': value.hex(), 'urr_directions': mask,
            'admitted': False, 'burst_semantics': 'proposed: max(1500 bytes, MBR * 100 ms)'}


def compile_image(registry):
    if (type(registry.get('schema')) is not int or registry['schema'] != 1 or
            registry.get('revoked') is not False):
        raise ValueError('Missing, revoked or unsupported native registry')
    sessions = registry['sessions']
    if not isinstance(sessions, list) or len(sessions) > 1024:
        raise ValueError('Invalid session registry')
    image, fallback = [], []
    identities, addresses, tunnels = set(), set(), set()
    for session in sessions:
        try:
            row = session_image(registry, session)
        except (KeyError, TypeError, ValueError) as exc:
            fallback.append({'ue': session.get('ue') if isinstance(session, dict) else None,
                             'reason': str(exc), 'effective_mode': 'kernel'})
            continue
        # Reject the whole image on ambiguity; never allow last-write-wins.
        seid = row['identity'][1]
        tunnel = (row['upf'], row['ul_teid'])
        if seid in identities or row['ue'] in addresses or tunnel in tunnels:
            raise ValueError('Ambiguous SEID, UE or uplink TEID in registry')
        identities.add(seid); addresses.add(row['ue']); tunnels.add(tunnel)
        image.append(row)
    return {'schema': 1, 'map_abi': 'c4_policy_v1', 'namespace': 'maestro-urllc',
            'n3': 'murllc-n3', 'n6': 'murllc-mec',
            'layout_ready': True,
            # Serialization alone cannot authorize forwarding or certify recovery.
            'admission_ready': bool(image) and not fallback and registry.get('ready') is True
                and registry.get('capabilities', {}).get('restart_replay') is True
                and all(s.get('identity_verified') is True and s.get('bridge_ready') is True
                        and s.get('fast_eligible') is True for s in sessions),
            'sessions': image, 'fallback': fallback,
            'blockers': ['Native shared QER takeover and burst transfer',
                         'URR/N4 importer and durable reconciliation',
                         'Complete PFCP eligibility and SUPI/PDU correlation']}
