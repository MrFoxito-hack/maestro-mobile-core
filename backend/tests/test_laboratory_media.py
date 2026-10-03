import pytest
from app.laboratory.qoe_media import read_media, save_media
from app.laboratory.qoe_transport import Lab


def test_campaign_media_reused_and_alteration_rejected(tmp_path):
    assets = {'index.m3u8': b'manifest', 'init.mp4': b'init', 'seg000.m4s': b'segment'}
    cache = tmp_path / 'media'
    save_media(cache, assets)
    assert read_media(cache) == assets
    (cache / 'seg000.m4s').write_bytes(b'other')
    with pytest.raises(ValueError, match='hash_mismatch'): read_media(cache)
    with pytest.raises(FileExistsError): save_media(cache, assets)


def test_guarded_upload_chunks_respect_argv_limit_and_stop_on_unknown():
    import base64
    host = Lab.__new__(Lab)
    host.guard_owner = {'private': True}
    host.check_ownership = lambda: None
    requests = []
    host.run = lambda args: requests.append(args)
    data = bytes(range(256)) * 600
    host.write('/owned/media', data)
    assert b''.join(base64.b64decode(args[4]) for args in requests) == data
    assert all(len(args[4]) < 50000 for args in requests)
    assert [int(args[6]) for args in requests] == list(range(0, len(data), 32768))
    attempts = []
    def lost(args):
        attempts.append(args)
        raise RuntimeError('unknown')
    host.run = lost
    with pytest.raises(RuntimeError, match='unknown'): host.write('/owned/media', data)
    assert len(attempts) == 1
