import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException
from app.services import terminal_media


@pytest.mark.parametrize('profile,asset', [('720p', '../secret'), ('480p', 'init.mp4'),
                                         ('1080p', 'seg030.m4s'), ('720p', 'http://host/file')])
def test_only_catalog_assets(profile, asset):
    with pytest.raises(HTTPException) as error:
        terminal_media.validate_asset(profile, asset)
    assert error.value.status_code == 404


def test_media_rejects_other_group_terminal(client, student_headers):
    assert client.get('/api/v1/terminal/media/720p/index.m3u8?imsi=imsi-999700000000002', headers=student_headers).status_code == 403


def test_no_simulated_video(client, teacher_headers):
    assert client.get('/api/v1/terminal/media/720p/init.mp4', headers=teacher_headers).status_code == 503


def test_relay_no_cache_and_failure_not_video(client, teacher_headers, monkeypatch):
    fetch = AsyncMock(return_value=b'fragment-test')
    monkeypatch.setattr(terminal_media, 'fetch', fetch)
    result = client.get('/api/v1/terminal/media/720p/init.mp4', headers=teacher_headers)
    assert result.content == b'fragment-test'
    assert result.headers['cache-control'] == 'no-store'
    fetch.side_effect = HTTPException(502, 'incomplete')
    result = client.get('/api/v1/terminal/media/720p/init.mp4', headers=teacher_headers)
    assert result.status_code == 502
    assert result.headers['content-type'].startswith('application/json')
