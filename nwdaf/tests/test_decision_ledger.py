from uuid import uuid4
import sqlite3
import pytest
from app.core.database import Database
from app.core.decision_ledger import DecisionLedger
from app.models import DecisionEvent


def test_order_deduplication_and_no_false_enforcement(tmp_path):
    db = Database(tmp_path/'ledger.db'); ledger = DecisionLedger(db)
    body = dict(event_id=uuid4(),decision_id=uuid4(),stage='DETECTED',supi='imsi-999700000000001',
                snssai={'sst':1},policy_id='pcc-1',action='mitigate',nominal_mbr_bps=20000000,
                target_mbr_bps=5000000,evidence_ref='fixture://not-a-live-decision')
    event = DecisionEvent(**body)
    assert ledger.append(event) == ledger.append(event)
    with pytest.raises(ValueError): ledger.append(event.model_copy(update={'target_mbr_bps':1}))
    with pytest.raises(ValueError): ledger.append(event.model_copy(update={'event_id':uuid4(),'stage':'ENFORCEMENT_VERIFIED'}))
    for stage in ['N7_SENT','N7_ACK']:
        ledger.append(event.model_copy(update={'event_id':uuid4(),'stage':stage}))
    assert ledger.list()['items'][0]['stage'] == 'N7_ACK'
    assert 'elapsed_ms' not in ledger.list()['items'][0]
    with db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError): connection.execute('DELETE FROM decision_events')
    ledger.append(event.model_copy(update={'event_id':uuid4(),'stage':'ENFORCEMENT_VERIFIED','elapsed_ms':125.0}))
    assert DecisionLedger(Database(db.path)).list()['items'][0]['elapsed_ms'] == 125.0
