import sqlite3

import pytest

from app.laboratory.remote_execution_guard import initialize, control


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr('app.laboratory.remote_execution_guard.os.getpgrp',lambda:12345,raising=False)
    db=sqlite3.connect(':memory:');initialize(db)
    now=[1.0]
    request={'execution_id':'a'*32,'token':'b'*32,'generation':1}
    def invoke(operation, **values):
        return control(db,{**request,'operation':operation,**values},clock=lambda:now[0],boot='boot',alive=lambda _:False)
    yield db,now,invoke
    db.close()


def test_old_token_and_late_old_recovery_cannot_retake_new_generation(guard):
    _,_,call=guard
    call('acquire')
    call('recover',token='c'*32,generation=2)
    with pytest.raises(ValueError,match='ownership_lost'):call('run',action_id='d'*32)
    with pytest.raises(ValueError,match='stale_generation'):call('recover')


def test_unknown_operation_not_replayed_and_reservation_not_freed_on_expiry(guard):
    _,now,call=guard
    call('acquire');call('run',action_id='d'*32)
    with pytest.raises(ValueError,match='already_attempted'):call('run',action_id='d'*32)
    with pytest.raises(ValueError,match='unknown_remote'):call('run',action_id='e'*32)
    now[0]=200
    with pytest.raises(ValueError,match='remote_resource_reserved'):call('acquire',execution_id='f'*32)
    call('recover',token='c'*32,generation=2)
    with pytest.raises(ValueError,match='recovery_not_verified'):call('release',token='c'*32,generation=2)
    call('release',token='c'*32,generation=2,recovery_verified=True)
    with pytest.raises(ValueError,match='retired'):call('acquire')


def test_recovery_waits_for_previous_command_process_group(guard):
    db,_,call=guard
    call('acquire');call('run',action_id='d'*32)
    with pytest.raises(ValueError,match='still_running'):
        control(db,{'operation':'recover','execution_id':'a'*32,'token':'c'*32,'generation':2},
                clock=lambda:2,boot='boot',alive=lambda _:True)
