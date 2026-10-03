from copy import deepcopy
import numpy as np
import pytest
from app.engine.slice_load import forecast
from app.engine.abnormal_behaviour import detect
from app.engine.service_experience import estimate


def signal(n=80):
    t = np.arange(n)
    return 35 + .15*t + 8*np.sin(2*np.pi*t/12)


def test_forecast_reproducible_and_held_out_accuracy():
    values = signal(86)
    first = forecast(values[:80], np.arange(80)*300)
    assert first == forecast(values[:80], np.arange(80)*300)
    for p, actual in zip(first['points'], values[[82, 85]]):
        assert abs(p['value']-actual) < 2
        assert 0 <= p['lower'] <= p['value'] <= p['upper'] <= 100
        assert p['calibration_samples'] == 20
    assert first['coverage_guaranteed'] is False


@pytest.mark.parametrize('kind', ['short','nan','gap','duplicate','outside'])
def test_forecast_rejects_unusable_data(kind):
    values = signal(); times = np.arange(80, dtype=float)*300
    if kind == 'short': values, times = values[:12], times[:12]
    if kind == 'nan': values[0] = np.nan
    if kind == 'gap': times[1] += 10
    if kind == 'duplicate': times[1] = times[0]
    if kind == 'outside': values[0] = 101
    with pytest.raises(ValueError): forecast(values, times)


def test_30_minute_data_cannot_predict_15_minute_grid():
    with pytest.raises(ValueError): forecast(signal(), np.arange(80)*1800, interval_seconds=1800)


def test_constant_load_and_coarse_grid():
    result = forecast([25.0]*80, np.arange(80)*1800, interval_seconds=1800, horizons=(1800,))
    assert result['points'][0]['value'] == 25
    assert result['points'][0]['lower'] == result['points'][0]['upper'] == 25


def test_mad_stable_baseline_spike_and_recovery():
    history = [(i*60, 10) for i in range(60)]
    assert not detect(history, 10, 3600)['anomaly']
    assert detect(history, 100, 3600)['anomaly']
    assert not detect(history+[(3600, 100)], 10, 3660)['anomaly']
    assert detect([], 10, 3600)['anomaly'] is None
    with pytest.raises(ValueError): detect(history, 10, 3500)


def av_input():
    return {
        'I11': {'segments': [{'start': 0, 'duration': 60, 'bitrate': 128, 'codec': 'aaclc'}], 'streamId': 1},
        'I13': {'segments': [{'start': 0, 'duration': 60, 'bitrate': 4000, 'codec': 'h264', 'fps': 25, 'resolution': '1920x1080'}], 'streamId': 1},
        'I23': {'stalling': [], 'streamId': 1},
        'IGen': {'device': 'mobile', 'displaySize': '1920x1080', 'viewingDistance': '30cm'},
    }


def test_p1203_real_model_stalls_reduce_qoe_and_input_is_unchanged():
    from itu_p1203 import P1203Standalone
    original = av_input(); unchanged = deepcopy(original)
    result = estimate(original)
    reference = P1203Standalone(original, quiet=True).calculate_complete()['O46']
    assert result['mos'] == pytest.approx(reference, abs=1e-10)
    assert original == unchanged
    stalled = av_input(); stalled['I23']['stalling'] = [[20, 12], [40, 8]]
    assert estimate(stalled)['mos'] < result['mos']


def test_p1203_rejects_fake_network_only_or_unsupported_video():
    with pytest.raises(ValueError): estimate({'bitrate': 1000, 'rtt': 20})
    data = av_input(); data['I13']['segments'][0]['resolution'] = '3840x2160'
    with pytest.raises(ValueError): estimate(data)
