import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'backend'))
from nwdaf_upf_pm import store_observation_v2, merge_snapshot_v2


def sample(**changes):
    return dict(boot_id='boot',netns_inode=1,ifindex=4,uptime=100,timestamp=1000,
                rx_bytes=100,tx_bytes=200,rx_packets=10,tx_packets=20,xdp_attached=False,**changes)


def test_three_upfs_do_not_share_state_and_xdp_is_excluded(tmp_path):
    path = tmp_path/'pm.db'
    def write(obj, data): return store_observation_v2(path,object_id=obj,observed=data)
    first = sample()
    second = {**first,'uptime':160,'timestamp':1060,'rx_bytes':700,'tx_bytes':500}
    assert write('nf:1',first) is None
    assert write('nf:2',first) is None
    assert write('nf:3',first) is None
    assert write('nf:1',second)['dl_bps'] == 40
    assert write('nf:2',{**second,'netns_inode':2}) is None
    assert write('nf:3',{**second,'xdp_attached':True}) is None
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT DISTINCT object_id,counter_id FROM samples_v2').fetchall()
    assert len(rows)==4 and all(r[0]=='nf:1' for r in rows)
    assert any(r[1]=='nwdaf.upf.dl.bps' for r in rows)


def test_snapshot_empty_when_all_targets_missing(tmp_path):
    source, dest = tmp_path/'history.db', tmp_path/'snapshot.db'
    with sqlite3.connect(dest) as db:
        db.executescript('CREATE TABLE metric_samples(collected_at,bucket_epoch,testbed_id,scenario_id,'
                         'object_id,counter_id,value,unit,source,quality);'
                         'CREATE TABLE export_metadata(row_count);INSERT INTO export_metadata VALUES(0);')
    merge_snapshot_v2(source,dest)
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT row_count FROM export_metadata').fetchone()[0] == 0
