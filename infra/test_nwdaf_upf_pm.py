from nwdaf_upf_pm import store_observation


def test_device_rates_reset_and_gaps(tmp_path):
    path = tmp_path/'history.db'
    common = dict(object_id='nf:upf-01', boot='boot1', ifindex=4)
    assert store_observation(path, **common, uptime=10, tx_bytes=100, timestamp=1000) is None
    assert store_observation(path, **common, uptime=20, tx_bytes=1100, timestamp=1010) == 800
    assert store_observation(path, **common, uptime=30, tx_bytes=1, timestamp=1020) is None
    assert store_observation(path, **common, uptime=200, tx_bytes=1001, timestamp=1190) is None
    assert store_observation(path, **{**common, 'ifindex': 5}, uptime=210, tx_bytes=2001, timestamp=1200) is None
    assert store_observation(path, **{**common, 'object_id': 'nf:upf-02'}, uptime=220, tx_bytes=3001, timestamp=1210) is None
