"""Correlate event-thread observations without inventing N7/N4 confirmation.

SEIDs and TEIDs are scoped to NF instances. IP is an observed attribute, never
the PDU identity. This registry is not a schema-2 effective-policy checkpoint:
the observer does not yet cover all writer boundaries or all PFCP IEs.
"""
from copy import deepcopy
import hashlib
import json

NFS = {'pcf', 'smf', 'smf2', 'smf3', 'upf', 'upf2', 'upf3'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def uint(value, bits=64):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 0 <= value < 2 ** bits:
        raise ValueError('invalid_native_unsigned_integer')
    return value


def rule_ids(rules):
    ids = {}
    for kind in ('pdr', 'far', 'qer', 'urr'):
        entries = rules[kind]
        if not isinstance(entries, list):
            raise ValueError('native_rules_not_observed')
        ids[kind] = set()
        for r in entries:
            rid = uint(r['id'], 32)
            if rid in ids[kind] or r['active'] is not True:
                raise ValueError('duplicate_or_inactive_native_rule')
            ids[kind].add(rid)
    for r in rules['pdr']:
        if (uint(r['far_id'], 32) not in ids['far'] or
                not {uint(i, 32) for i in r['qer_ids']} <= ids['qer'] or
                not {uint(i, 32) for i in r['urr_ids']} <= ids['urr']):
            raise ValueError('dangling_native_rule_reference')
    return ids


def correlate(observations, smf_addresses):
    if set(observations) != NFS or set(smf_addresses) != {'smf', 'smf2', 'smf3'}:
        raise ValueError('seven_native_observations_required')
    instances = {}
    for name, nf in observations.items():
        if (nf.get('scope') != 'native_observation' or nf.get('schema_version') != 1
                or not nf.get('boot_id') or not uint(nf['generation'])
                or not isinstance(nf['sessions'], list)):
            raise ValueError('invalid_native_observation')
        instances[name] = {k: nf[k] for k in ('boot_id', 'generation', 'pid', 'writer_fenced')}
    pcf = {}
    for s in observations['pcf']['sessions']:
        key = (s['supi'], uint(s['pdu_id'], 8), s['dnn'])
        if key in pcf:
            raise ValueError('ambiguous_pcf_identity')
        pcf[key] = s
    upfs = []
    for name in ('upf', 'upf2', 'upf3'):
        for s in observations[name]['sessions']:
            rule_ids(s['rules'])
            upfs.append((name, s))
    rows, claimed, identities = [], set(), set()
    for smf in ('smf', 'smf2', 'smf3'):
        for s in observations[smf]['sessions']:
            if not uint(s['smf_seid']) or not uint(s['upf_seid']):
                raise ValueError('native_session_establishing')
            identity = (s['supi'], uint(s['pdu_id'], 8), s['dnn'])
            if identity in identities:
                raise ValueError('ambiguous_smf_identity')
            identities.add(identity)
            policy = pcf.get(identity)
            if not policy or not s['policy_id'] or policy['policy_id'] != s['policy_id']:
                raise ValueError('pcf_smf_policy_not_correlated')
            matches = [(i, name, u) for i, (name, u) in enumerate(upfs)
                       if (u['smf_ipv4'] in smf_addresses[smf]
                           and uint(u['smf_seid']) == uint(s['smf_seid'])
                           and uint(u['upf_seid']) == uint(s['upf_seid'])
                           and u['dnn'] == s['dnn'])]
            if len(matches) != 1 or matches[0][0] in claimed:
                raise ValueError('smf_upf_seids_not_uniquely_correlated')
            i, name, upf = matches[0]; claimed.add(i)
            rule_ids(s['rules'])
            key = {'supi': s['supi'], 'pdu_id': uint(s['pdu_id'], 8), 'smf': smf, 'upf': name,
                   'smf_seid': str(uint(s['smf_seid'])), 'upf_seid': str(uint(s['upf_seid'])),
                   'smf_instance': instances[smf], 'upf_instance': instances[name],
                   'smf_context': uint(s['context_id']), 'upf_context': uint(upf['context_id'])}
            rows.append({
                'identity': key, 'session_key': fingerprint(key), 'dnn': s['dnn'],
                'ue_ipv4': upf['ue_ipv4'], 'pcf_observation': deepcopy(policy),
                'smf_rules': deepcopy(s['rules']), 'upf_rules': deepcopy(upf['rules']),
                'rules_sha256': fingerprint(upf['rules']), 'usage': deepcopy(upf['usage']),
                # Matching state alone cannot prove transaction ACK or exclusion.
                'n7_confirmed': False, 'n4_confirmed': False, 'xdp_eligible': False,
                'admission_error': 'native_writer_fencing_not_implemented',
            })
    if len(claimed) != len(upfs) or identities != set(pcf):
        raise ValueError('uncorrelated_native_sessions')
    return {'schema_version': 1, 'scope': 'correlated_native_observation', 'nfs': instances,
            'sessions': sorted(rows, key=lambda row: (row['identity']['supi'], row['identity']['pdu_id'])),
            'admission_available': False, 'checkpoint_eligible': False}
