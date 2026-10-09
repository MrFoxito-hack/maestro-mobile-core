import asyncio
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.core.config import get_settings
from app.services import terminal_sessions, terminal


def test_unaccepted_urllc_cannot_stop_live_ue(monkeypatch):
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', False)
    remote = AsyncMock()
    monkeypatch.setattr(terminal, 'adapter', lambda:remote)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(terminal_sessions.select('5g-plus'))
    assert failure.value.status_code == 409
    remote.stop_service.assert_not_called()


def test_profiles_keep_legacy_corporate_until_migration(monkeypatch):
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', False)
    assert terminal_sessions.expected_slice('corporate') == {'sst':1,'sd':2}
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    assert terminal_sessions.expected_slice('corporate') == {'sst':3,'sd':3}
    assert terminal_sessions.expected_slice('5g-plus') == {'sst':2,'sd':2}


def test_same_sd_different_sst_is_not_same_slice():
    assert not terminal_sessions.matches_slice({'sst':1,'sd':2}, {'sst':2,'sd':2})
    assert terminal_sessions.matches_slice({'sst':2,'sd':'000002'}, {'sst':2,'sd':2})
    assert not terminal_sessions.matches_slice(None, {'sst':2,'sd':2})


def test_profile_endpoint_does_not_advertise_unaccepted_cutover(client,teacher_headers,monkeypatch):
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', False)
    result = client.get('/api/v1/terminal/profiles',headers=teacher_headers)
    assert result.status_code == 200
    data = result.json()
    assert not data['migration_complete']
    assert next(p for p in data['profiles'] if p['id']=='urllc')['enabled'] is False
