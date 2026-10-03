"""Synthetic audiovisual metadata exercises the conversion, not a real QoE run."""
import importlib.util

import pytest

from app.laboratory.qoe_metrics import p1203_document, score_p1203_locally
from app.laboratory.player_observation import startup_from_player


def inputs():
    probe = {'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'width': 640, 'height': 360,
         'time_base': '1/25', 'avg_frame_rate': '25/1'},
        {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac', 'profile': 'LC', 'time_base': '1/48000'},
    ], 'packets': [
        {'stream_index': 0, 'pts': 0, 'duration': 125, 'size': 100000},
        {'stream_index': 0, 'pts': 125, 'duration': 125, 'size': 100000},
        {'stream_index': 1, 'pts': 0, 'duration': 240000, 'size': 10000},
        {'stream_index': 1, 'pts': 240000, 'duration': 240000, 'size': 10000},
    ]}
    trace = {'startup_seconds': 1, 'firstPlaying': 1100, 'requested': 100, 'ended': True, 'stalls': [[4, .25]]}
    return probe, trace


def test_player_and_packet_units_are_conserved():
    probe, trace = inputs()
    data = p1203_document(probe, trace)
    assert data['I13']['segments'][0]['duration'] == 10
    assert data['I13']['segments'][0]['bitrate'] == 160
    assert data['I11']['segments'][0]['bitrate'] == 16
    assert data['I23']['stalling'] == [[0, 1], [4, .25]]
    assert trace['stalls'] == [[4, .25]]


@pytest.mark.parametrize('value', [None, float('nan'), -1, True])
def test_missing_or_invalid_startup_never_becomes_zero(value):
    probe, trace = inputs()
    trace['startup_seconds'] = value
    with pytest.raises(ValueError, match='missing_player_startup'): p1203_document(probe, trace)


def test_incomplete_or_inconsistent_player_is_rejected():
    probe, trace = inputs()
    trace['firstPlaying'] = 900
    with pytest.raises(ValueError, match='incomplete_or_inconsistent'): p1203_document(probe, trace)


def test_absent_optional_estimator_does_not_fabricate_mos_or_publish_to_core(monkeypatch):
    monkeypatch.setattr(importlib.util, 'find_spec', lambda _: None)
    result = score_p1203_locally(p1203_document(*inputs()))
    assert result == {'mos': None, 'status': 'unavailable', 'reason': 'p1203_dependency_not_installed'}


def test_offline_startup_requires_consistent_clock_and_original_playing_event():
    _, trace = inputs()
    assert startup_from_player(trace)['measurement_validity'] == 'inconclusive'
    trace['events'] = [{'type': 'playing', 'monotonic_ms': 1100}, {'type': 'ended', 'monotonic_ms': 11100}]
    result = startup_from_player(trace)
    assert result['startup_seconds'] == 1 and result['measurement_validity'] == 'valid'
    trace['startup_seconds'] = 0.5
    assert startup_from_player(trace)['startup_seconds'] is None
