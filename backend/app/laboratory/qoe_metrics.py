"""P.1203 input derived from actual ffprobe packets and player events."""
from fractions import Fraction
import math


def score_p1203_locally(document):
    """Reuse the existing pinned research estimator without publishing to NWDAF.

    Missing dependencies produce an absent estimate, never a network fallback or
    an invented score. The primary measured player startup stays independent.
    """
    import importlib.util
    from pathlib import Path
    if importlib.util.find_spec('itu_p1203') is None:
        return {'mos': None, 'status': 'unavailable', 'reason': 'p1203_dependency_not_installed'}
    source = Path(__file__).resolve().parents[3] / 'nwdaf/app/engine/service_experience.py'
    if not source.is_file():
        return {'mos': None, 'status': 'unavailable', 'reason': 'p1203_estimator_not_packaged'}
    spec = importlib.util.spec_from_file_location('_maestro_offline_p1203', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return {**module.estimate(document), 'status': 'estimated', 'network_access': False}
    except (ValueError, RuntimeError, ImportError):
        return {'mos': None, 'status': 'unavailable', 'reason': 'p1203_profile_or_version_unvalidated'}


def p1203_document(probe, trace):
    document={'I11':{'segments':[],'streamId':1},'I13':{'segments':[],'streamId':1},
        'I23':{'stalling':[],'streamId':1},'IGen':{'device':'mobile','displaySize':'640x360','viewingDistance':'30cm'}}
    for stream in probe['streams']:
        packets=[p for p in probe['packets'] if p['stream_index']==stream['index']]
        ordered=sorted(packets,key=lambda p:int(p['pts']))
        ticks=[int(b['pts'])-int(a['pts']) for a,b in zip(ordered,ordered[1:])]
        last_duration=ordered[-1].get('duration')
        if last_duration is None:
            # MP4 demuxing may omit packet duration. Derive the final frame
            # duration only when every observed PTS interval is identical.
            assert ticks and len(set(ticks))==1 and ticks[0]>0, 'Variable frame timing needs explicit duration metadata'
            last_duration=ticks[0]
        duration=float((int(ordered[-1]['pts'])+int(last_duration)-int(ordered[0]['pts']))*Fraction(stream['time_base']))
        segment={'start':0,'duration':duration,'bitrate':sum(int(p['size']) for p in packets)*8/duration/1000}
        if stream['codec_type']=='video':
            assert stream['codec_name']=='h264'
            segment.update(codec='h264',fps=float(Fraction(stream['avg_frame_rate'])),resolution=f"{stream['width']}x{stream['height']}")
            document['I13']['segments'].append(segment)
        elif stream['codec_type']=='audio':
            assert stream['codec_name']=='aac' and stream['profile']=='LC'
            segment.update(codec='aaclc');document['I11']['segments'].append(segment)
    assert len(document['I11']['segments'])==len(document['I13']['segments'])==1

    startup = trace.get('startup_seconds')
    if type(startup) not in (float, int) or not math.isfinite(startup) or startup < 0:
        raise ValueError('missing_player_startup')
    if not trace.get('ended') or not math.isclose((trace['firstPlaying'] - trace['requested']) / 1000, startup, abs_tol=1e-6):
        raise ValueError('incomplete_or_inconsistent_player')
    document['I23']['stalling'] = [[0, startup], *trace['stalls']]
    return document
