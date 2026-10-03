from datetime import datetime, timezone
import json
import time

from app.core.database import Database
from app.forecast_view import predictions


def test_live_samples_keep_model_history_and_slice_selection_stable(tmp_path):
    db = Database(tmp_path/'view.db')
    now = time.time()
    expiry = {'expiry': datetime.fromtimestamp(now+300, timezone.utc).isoformat()}
    target = json.dumps({'sst': 1, 'sd': '000001'}, sort_keys=True)
    history = [{'timestamp': now-300, 'value': 10}, {'timestamp': now, 'value': 20}]
    db.record('LOAD_LEVEL_INFORMATION', target, {'forecast': 1},
              {'model': 'forecast', 'history': history, 'points': [{'value': 22}]}, expiry)
    for index in range(3):
        db.record('LOAD_LEVEL_INFORMATION', target, {'live': index},
                  {'model': 'upf-counter-delta-v1', 'points': [],
                   'history': [{'timestamp': now+index, 'value': index}]}, expiry)
    corporate = json.dumps({'sst': 1, 'sd': '000002'}, sort_keys=True)
    db.record('LOAD_LEVEL_INFORMATION', corporate, {'live': 0},
              {'model': 'upf-counter-delta-v1', 'points': [],
               'history': [{'timestamp': now, 'value': 0}]}, expiry)
    items = predictions(db)
    assert items[0]['snssai']['sd'] == '000001'
    assert items[0]['evidence']['history'] == history
    assert items[0]['evidence']['points'] == [{'value': 22}]
    assert len(items[0]['observation']['evidence']['history']) == 3
    assert items[1]['evidence']['points'] == []
    assert items[1]['evidence']['status'] == 'insufficient_history'


def test_expired_live_sample_does_not_make_forecast_fresh(tmp_path):
    db = Database(tmp_path/'view.db')
    expired = {'expiry': datetime.fromtimestamp(time.time()-10, timezone.utc).isoformat()}
    target = json.dumps({'sst': 1})
    db.record('LOAD_LEVEL_INFORMATION', target, {'test': 1},
              {'model': 'upf-counter-delta-v1', 'points': [], 'history': []}, expired)
    assert predictions(db)[0]['observation']['fresh'] is False
