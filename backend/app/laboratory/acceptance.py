"""Implementation gates, never permission derived from a request or an active NF.

An explicitly assigned operational pilot can run through the remote transport
guard. Full Core acceptance still needs policy recovery and other-writer fencing.
"""

REAL_BLOCKERS = (
    'effective_policy_checkpoint_unavailable',
    'remote_writer_fencing_unavailable',
    'independent_policy_recovery_unverified',
    'shared_bottleneck_not_calibrated',
)


class RealPreflightBlocked(ValueError):
    pass


def require_real_actuator(context=None):
    """Admit the explicitly assigned operational pilot, not full Core acceptance.

    This record is produced by the worker after live checks. It is not accepted
    in any public request schema. Effective policy recovery remains a separate
    scientific validity condition and G2 cannot be inferred from this admission.
    """
    admission=(context or {}).get('admission',{})
    if (admission.get('mode')!='real' or admission.get('live_preflight_passed') is not True
            or admission.get('protocol')!='qoe_operational_pilot_v1'):
        raise RealPreflightBlocked('real_actuator_not_accepted')


def real_availability():
    return {'real_pilot_available': True, 'real_preflight_available': True,
            'acceptance_scope':'operational_pilot', 'full_core_acceptance':False,
            'execution_ready': False, 'execution_blockers': list(REAL_BLOCKERS)}
