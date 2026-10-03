"""Reconstruct player startup from the conserved browser clock and event record."""
import math


def startup_from_player(trace):
    result = {'metric': 'player_startup_delay_seconds', 'unit': 'seconds',
              'startup_seconds': None, 'measurement_validity': 'inconclusive', 'reasons': []}
    values = [trace.get(key) for key in ('requested', 'firstPlaying', 'startup_seconds')]
    if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values):
        result['reasons'].append('player_clock_or_measurement_missing')
        return result
    requested, playing, saved = values
    if playing < requested:
        result['reasons'].append('player_clock_order_invalid')
        return result
    calculated = (playing - requested) / 1000
    if not math.isclose(calculated, saved, rel_tol=0, abs_tol=1e-6):
        result['reasons'].append('stored_startup_does_not_match_clock')
        return result
    events = trace.get('events') or []
    observed = next((e.get('monotonic_ms') for e in events if e.get('type') == 'playing'), None)
    if type(observed) not in (int, float) or not math.isfinite(observed) or not math.isclose(observed, playing, rel_tol=0, abs_tol=1e-6):
        result['reasons'].append('first_playing_event_not_verified')
    if trace.get('ended') is not True or not any(e.get('type') == 'ended' for e in events):
        result['reasons'].append('player_completion_not_verified')
    result['startup_seconds'] = calculated
    if not result['reasons']: result['measurement_validity'] = 'valid'
    return result
