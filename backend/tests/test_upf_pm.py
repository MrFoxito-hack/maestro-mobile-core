import asyncio
from app.services import upf_pm
from app.services.upf_inventory import inventory


def reading(**changes):
    return {'boot_id': 'a', 'netns_inode': 7, 'ifindex': 4, 'uptime': 100,
            'timestamp': 1000, 'rx_bytes': 100, 'tx_bytes': 200,
            'rx_packets': 10, 'tx_packets': 20, 'xdp_attached': False,
            'prometheus': 'fivegs_upffunction_upf_sessionnbr 2\npfcp_peers_active 1', **changes}


def test_tun_direction_and_device_continuity():
    before = reading()
    after = reading(uptime=105, timestamp=1005, rx_bytes=1100, tx_bytes=700, rx_packets=20)
    assert upf_pm.rate(before, after) == {'ul_bps': 1600, 'dl_bps': 800, 'ul_pps': 2, 'dl_pps': 0}
    for change in ({'boot_id':'b'}, {'netns_inode':8}, {'ifindex':5}, {'rx_bytes':0}, {'timestamp':1100}):
        assert upf_pm.rate(before, {**after, **change}) is None


def test_one_failed_target_does_not_hide_peers(monkeypatch):
    async def probe(target):
        if target['service'] == 'urllc':
            raise RuntimeError('Namespace not provisioned')
        return reading()
    monkeypatch.setattr(upf_pm, 'read_target', probe)
    collector = upf_pm.UpfCollector()
    asyncio.run(collector.collect())
    states = {t['service']: t for t in collector.snapshot()['targets']}
    assert states['urllc']['status'] == 'unavailable'
    assert states['embb']['metrics']['active_sessions'] == 2
    assert states['miot']['status'] == 'warming_up'
    assert states['urllc']['metrics'] == {}


def test_xdp_never_claims_complete_traffic(monkeypatch):
    async def probe(_): return reading(xdp_attached=True)
    monkeypatch.setattr(upf_pm, 'read_target', probe)
    collector = upf_pm.UpfCollector()
    asyncio.run(collector.collect())
    assert all(not t['traffic_complete'] for t in collector.snapshot()['targets'])


def test_stale_data_is_not_live_zero(monkeypatch):
    collector = upf_pm.UpfCollector()
    collector.latest['upf-01'] = {'collected_at': 100, 'status':'measured',
                                'metrics':{'dl_bps':500}, 'traffic_complete':True}
    monkeypatch.setattr(upf_pm.time, 'time', lambda: 200)
    item = collector.snapshot()['targets'][0]
    assert item['status'] == 'stale' and item['metrics'] == {} and not item['traffic_complete']


def test_new_miot_identity_does_not_relabel_old_history():
    miot = next(t for t in inventory()['targets'] if t['service'] == 'miot')
    assert miot['pm_object_id'] != 'nf:upf-02'
    assert (miot['sst'], miot['sd']) == (3, '000003')


def test_snapshot_access_control(client, teacher_headers, student_headers):
    assert client.get('/api/v1/upf-telemetry/snapshot').status_code == 401
    assert client.get('/api/v1/upf-telemetry/snapshot', headers=student_headers).status_code == 403
    response = client.get('/api/v1/upf-telemetry/snapshot', headers=teacher_headers)
    assert response.status_code == 200 and len(response.json()['targets']) == 3
