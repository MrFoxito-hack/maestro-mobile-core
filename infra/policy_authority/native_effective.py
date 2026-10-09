"""Construct policy-only observations from the bounded IPv4 Session-AMBR profile.

This module never upgrades a native writer-coverage assertion. A partial
observer therefore produces an incomplete checkpoint, rejected by Authority.
"""
from copy import deepcopy
import hashlib
import json

if __package__:
    from .native_registry import correlate, uint
else:
    from native_registry import correlate, uint


SMF_ADDRESSES = {'smf': ['10.210.50.1'], 'smf2': ['10.210.50.2'], 'smf3': ['10.210.50.18']}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def policy_projection(snapshot):
    return deepcopy({'mode': snapshot['mode'], 'sessions': sorted([
        {key: s[key] for key in ('supi', 'pdu_id', 'dnn', 'smf', 'upf', 'smf_seid', 'upf_seid', 'generation', 'policy')}
        for s in snapshot['sessions']], key=lambda s: (s['supi'], s['pdu_id']))})


def policy_without_charging(snapshot):
    value = policy_projection(snapshot)
    for row in value['sessions']:
        row['policy']['upf'].pop('urr', None)
        row['policy']['smf'].get('rules', {}).pop('urr', None)
    return value


def intended_policy(baseline, command):
    """Exact bounded policy projection for reconciling a lost commit reply."""
    value = policy_without_charging(baseline)
    if command.get('operation') == 'mode' and set(command) == {'operation', 'mode'}:
        if command['mode'] not in {'MANUAL', 'AUTONOMOUS'}:
            raise ValueError('invalid_native_mode')
        value['mode'] = command['mode']
        return value
    if command.get('operation') != 'qos' or set(command) - {
            'operation', 'supi', 'pdu_id', 'dnn', 'five_qi', 'mbr_ul_mbps', 'mbr_dl_mbps'}:
        raise ValueError('unsupported_native_commit_intent')
    matches = [r for r in value['sessions'] if r['supi'] == command.get('supi') and
               all(k not in command or command[k] == r[k] for k in ('pdu_id', 'dnn'))]
    if len(matches) != 1:
        raise ValueError('native_commit_session_ambiguous')
    policy = matches[0]['policy']
    if command.get('five_qi', policy['pcf']['five_qi']) != policy['pcf']['five_qi']:
        raise ValueError('unsupported_native_commit_5qi')
    for direction in ('ul', 'dl'):
        key = 'mbr_' + direction
        rate = command.get(key + '_mbps', int(policy['pcf'][key]) / 1e6)
        if type(rate) not in (int, float) or not .001 <= rate <= 100000 or abs(rate * 1000 - round(rate * 1000)) > 1e-6:
            raise ValueError('invalid_native_commit_mbr')
        value_bps = str(round(rate * 1e6))
        for layer in ('pcf', 'smf'):
            policy[layer][key] = value_bps
        for rules in (policy['smf']['rules'], policy['upf']):
            if len(rules['qer']) != 1:
                raise ValueError('unsupported_native_commit_qer')
            rules['qer'][0][key] = value_bps
    return value


def effective_sessions(observations, *, n7_confirmed=False, n4_confirmed=False):
    """Correlate effective policy independently of durable commit versions.

    Used to reconcile an interrupted version fanout; this alone is never an
    admissible checkpoint and does not upgrade native writer coverage.
    """
    registry = correlate(observations, SMF_ADDRESSES)
    sessions = []
    for row in registry['sessions']:
        identity = row['identity']
        smf = next(s for s in observations[identity['smf']]['sessions']
                   if uint(s['smf_seid']) == uint(identity['smf_seid']))
        pcf = row['pcf_observation']
        rules = deepcopy(row['upf_rules'])
        if (not isinstance(pcf.get('policy'), dict) or not isinstance(smf.get('policy'), dict) or
                pcf['policy'] != smf['policy'] or len(rules['qer']) != 1 or
                row['smf_rules']['qer'] != rules['qer']):
            raise ValueError('native_session_ambr_not_confirmed')
        policy = pcf['policy']
        if any(uint(rules['qer'][0][key]) != uint(policy[key]) for key in ('mbr_ul', 'mbr_dl')):
            raise ValueError('native_qer_differs_from_session_ambr')
        # Reconciliation always preserves the live charging configuration;
        # SMF policy excludes its cached URR grant as well as CHF state.
        smf_policy = deepcopy(smf['policy'])
        smf_policy['rules'] = {kind: deepcopy(row['smf_rules'][kind]) for kind in ('pdr', 'far', 'qer')}
        sessions.append({**{k: identity[k] for k in ('supi', 'pdu_id', 'smf', 'upf', 'smf_seid', 'upf_seid')},
                         'dnn': row['dnn'], 'generation': row['session_key'],
                         'n7_confirmed': n7_confirmed, 'n4_confirmed': n4_confirmed,
                         'rules': rules, 'policy': {'pcf': deepcopy(policy), 'smf': smf_policy, 'upf': rules}})
    return sessions


def checkpoint(observations, *, boot, measured_at, metadata):
    guards = [nf['fencing'] for nf in observations.values()]
    tokens = {uint(g['token']) for g in guards}
    versions = {uint(g['version']) for g in guards}
    if len(tokens) != 1 or len(versions) != 1:
        raise ValueError('native_checkpoint_fence_or_version_divergence')
    pending_n7 = [name for name, nf in observations.items() if nf['fencing']['pending_n7']]
    pending_n4 = [name for name, nf in observations.items() if nf['fencing']['pending_n4'] or nf['pending_native']]
    covered = all(nf['writer_fenced'] is True and nf['policy_complete'] is True and
                  nf['fencing']['enabled'] and not nf['fencing']['failed'] and
                  not nf['fencing']['recovery_required'] for nf in observations.values())
    sessions = effective_sessions(observations,
        n7_confirmed=bool(covered and not pending_n7 and not pending_n4),
        n4_confirmed=bool(covered and not pending_n4))
    value = {'schema_version': 2, 'scope': 'effective_policy', 'clock_boot_id': boot,
             'measured_at': measured_at, 'complete': covered and not pending_n7 and not pending_n4,
             'pending_n7': pending_n7, 'pending_n4': pending_n4,
             'version': versions.pop(), 'fencing_token': tokens.pop(), 'mode': observations['pcf']['mode'],
             'nfs': {name: {'boot_id': nf['boot_id'], 'generation': uint(nf['generation']),
                           'writer_fenced': nf['writer_fenced']} for name, nf in observations.items()},
             'sessions': sessions}
    if 'last_action' in metadata:
        value['last_action'] = metadata['last_action']
    value['policy_sha256'] = digest(policy_projection(value))
    return value
