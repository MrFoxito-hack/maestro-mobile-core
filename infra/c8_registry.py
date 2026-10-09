"""C8 renewal guard: permit generation refresh only for unchanged effective rules."""
def policy_signature(registry):
    return {
        'pid':registry['pid'], 'instance':registry['instance'],
        'sessions':sorted([
            {**{k:s[k] for k in ['ue','seid','dnn','pdrs','qers']},
             'urrs':[{k:v for k,v in u.items() if k not in ['native_bytes','native_packets']}
                     for u in s['urrs']]}
            for s in registry['sessions']],key=lambda s:s['ue'])}


def validate_renewal(original, current):
    if current['revoked'] or not all(s['bridge_ready'] and s['fast_eligible'] for s in current['sessions']):
        raise ValueError('renewal_not_eligible')
    if policy_signature(original)!=policy_signature(current):
        raise ValueError('effective_policy_or_session_changed')
    old={s['ue']:int(s['generation']) for s in original['sessions']}
    if any(int(s['generation'])<old[s['ue']] for s in current['sessions']):
        raise ValueError('generation_went_backwards')
