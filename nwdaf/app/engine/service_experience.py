"""Research adapter to pinned P.1203 implementation; never substitutes G.107.

Dependency has a restrictive research license and is deliberately not vendored.
"""
from copy import deepcopy
from importlib.metadata import version
from math import isfinite


def estimate(document):
    from itu_p1203 import P1203Standalone
    if version('itu-p1203') != '1.8.3':
        raise RuntimeError('Unvalidated P.1203 dependency version')
    data = deepcopy(document)
    for key in ('I11', 'I13', 'I23', 'IGen'):
        if key not in data:
            raise ValueError(f'Missing P.1203 input: {key}')
    ends = []
    for key in ('I11', 'I13'):
        segments = data[key].get('segments', [])
        if not 1 <= len(segments) <= 5000:
            raise ValueError('Missing or excessive audiovisual segments')
        end = 0.0
        for s in segments:
            numbers = [s.get(k) for k in ('start', 'duration', 'bitrate')]
            if any(not isinstance(v, (int, float)) or not isfinite(v) for v in numbers):
                raise ValueError('Invalid segment measurements')
            if abs(s['start']-end) > .01 or s['duration'] <= 0 or s['bitrate'] <= 0:
                raise ValueError('Segments must be contiguous, ordered and positive')
            end = s['start'] + s['duration']
            if key == 'I13':
                if s.get('codec') != 'h264' or not 0 < s.get('fps', 0) <= 25:
                    raise ValueError('Validated profile requires H.264 <=25fps')
                w, h = map(int, s['resolution'].split('x'))
                if not (0 < w <= 1920 and 0 < h <= 1080):
                    raise ValueError('Resolution outside validated P.1203 profile')
            elif s.get('codec') not in ('aaclc', 'heaac', 'mp2', 'ac3'):
                raise ValueError('Unsupported audio codec')
        ends.append(end)
    if abs(ends[0]-ends[1]) > .05 or not 8 <= max(ends) <= 300:
        raise ValueError('Require aligned audio/video, duration 8..300 seconds')
    stalls = data['I23'].get('stalling')
    if not isinstance(stalls, list) or any(len(x) != 2 or any(not isfinite(v) for v in x)
            or not 0 <= x[0] <= ends[0] or x[1] < 0 for x in stalls):
        raise ValueError('Explicit valid stalling observations required')
    score = float(P1203Standalone(data, quiet=True).calculate_complete()['O46'])
    if not isfinite(score) or not 1 <= score <= 5:
        raise RuntimeError('Invalid P.1203 output')
    return {'mos': score, 'model': 'ITU-T P.1203 mode 0', 'implementation': 'itu-p1203 1.8.3',
            'provenance': 'audiovisual segment metadata and player stalls', 'research_only': True}
