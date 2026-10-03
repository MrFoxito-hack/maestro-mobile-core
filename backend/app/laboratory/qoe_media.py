"""One immutable media set per campaign, reused byte-for-byte for both arms."""
import hashlib
import json
import re


def save_media(directory, assets):
    directory.mkdir(exist_ok=False)
    hashes = {}
    for name, payload in assets.items():
        if not re.fullmatch(r'index\.m3u8|init\.mp4|seg\d{3}\.m4s', name):
            raise ValueError('invalid_media_asset')
        with (directory / name).open('xb') as file:
            file.write(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    with (directory / 'hashes.json').open('x', encoding='utf-8') as file:
        json.dump(hashes, file, sort_keys=True)


def read_media(directory):
    hashes = json.loads((directory / 'hashes.json').read_text(encoding='utf-8'))
    if not {'index.m3u8', 'init.mp4', 'seg000.m4s'} <= hashes.keys():
        raise ValueError('incomplete_media_cache')
    if len(hashes) > 32:
        raise ValueError('media_cache_limit')
    assets = {}
    for name, expected in hashes.items():
        if not re.fullmatch(r'index\.m3u8|init\.mp4|seg\d{3}\.m4s', name):
            raise ValueError('invalid_media_asset')
        path = directory / name
        if path.is_symlink() or path.stat().st_size > 3_000_000:
            raise ValueError('invalid_media_file')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('media_cache_hash_mismatch')
        assets[name] = data
    if sum(map(len, assets.values())) > 3_000_000:
        raise ValueError('media_cache_budget')
    return assets
