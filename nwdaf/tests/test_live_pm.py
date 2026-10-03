from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.ingestion.live_pm import observation


def measured_pair():
    before = dict(boot_id='01234567-0123-0123-0123-012345678901', ifindex=3,
                  uptime=100.0, tx_bytes=1000, timestamp=1000.0)
    after = {**before, 'uptime': 101.0, 'timestamp': 1001.0, 'tx_bytes': 2376000}
    return {'object_id': 'nf:upf-01', 'before': before, 'after': after}


MAPPING = [SimpleNamespace(object_id='nf:upf-01', counter_id='nwdaf.slice.dl.bps', capacity_units=20000000)]


def test_counter_rate_uses_approved_budget():
    _, result = observation(measured_pair(), MAPPING, 1001.1)
    assert result['observed_bps'] == 19000000
    assert result['observed_percentage_unclipped'] == 95
    assert result['points'] == []


@pytest.mark.parametrize('key,value', [('boot_id','99999999-0123-0123-0123-012345678901'),
    ('ifindex',4), ('uptime',100.1), ('tx_bytes',0), ('timestamp',999.0)])
def test_rejects_discontinuous_readings(key, value):
    body=deepcopy(measured_pair()); body['after'][key]=value
    with pytest.raises(ValueError): observation(body, MAPPING, 1001.1)


def test_rejects_stale_and_unmapped_counters():
    with pytest.raises(ValueError): observation(measured_pair(), MAPPING, 1010.0)
    with pytest.raises(ValueError): observation(measured_pair(), [], 1001.1)
