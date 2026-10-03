"""Browser-reported playback observations, distinct from NWDAF P.1203 MOS."""
import json
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.db import transaction
from app.services.terminal_sessions import key


class PlaybackObservation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    imsi: str = Field(pattern=r'^imsi-\d{14,15}$')
    session_id: UUID
    sequence: int = Field(ge=0, le=1_000_000)
    profile: Literal['720p', '1080p']
    state: Literal['loading', 'playing', 'buffering', 'paused', 'ended', 'stopped']
    startup_seconds: float | None = Field(default=None, ge=0, le=86400)
    played_seconds: float = Field(ge=0, le=86400)
    rebuffer_count: int = Field(ge=0, le=100000)
    rebuffer_seconds: float = Field(ge=0, le=86400)
    received_bytes: int = Field(ge=0, le=1_000_000_000_000)


def _publish_p1203(body: PlaybackObservation):
    if body.played_seconds < 8.0 or body.startup_seconds is None or body.received_bytes <= 0:
        return
    try:
        from app.services.nwdaf import nwdaf_request
        duration = float(min(300.0, max(8.0, body.played_seconds)))
        bitrate_kbps = max(500.0, (body.received_bytes * 8.0 / duration / 1000.0))
        stalls = [[0.0, float(body.startup_seconds)]]
        if body.rebuffer_count > 0 and body.rebuffer_seconds > 0:
            step = duration / (body.rebuffer_count + 1)
            dur_per_stall = body.rebuffer_seconds / body.rebuffer_count
            for i in range(body.rebuffer_count):
                stalls.append([round((i + 1) * step, 2), round(dur_per_stall, 2)])
        resolution = '1920x1080' if body.profile == '1080p' else '1280x720'
        doc = {
            'I11': {'segments': [{'start': 0.0, 'duration': duration, 'bitrate': 128.0, 'codec': 'aaclc'}], 'streamId': 1},
            'I13': {'segments': [{'start': 0.0, 'duration': duration, 'bitrate': round(bitrate_kbps, 1), 'codec': 'h264', 'fps': 25.0, 'resolution': resolution}], 'streamId': 1},
            'I23': {'stalling': stalls, 'streamId': 1},
            'IGen': {'device': 'mobile', 'displaySize': resolution, 'viewingDistance': '30cm'},
        }
        nwdaf_request('/management/v1/publish/service-experience', method='POST', body=json.dumps({
            'supi': body.imsi,
            'app_id': 'stream5g',
            'timestamp': time.time(),
            'document': doc
        }))
    except Exception:
        # Failure to publish to NWDAF must not fail recording player observation
        pass


def record(body: PlaybackObservation):
    now = time.time()
    payload = body.model_dump(mode='json')
    with transaction() as db:
        db.execute('DELETE FROM terminal_playback WHERE received_at<?', (now-86400,))
        db.execute('''INSERT INTO terminal_playback VALUES(?,?,?,?,?,?)
            ON CONFLICT(terminal_key,session_id) DO UPDATE SET
            sequence=excluded.sequence,received_at=excluded.received_at,payload=excluded.payload
            WHERE excluded.sequence>terminal_playback.sequence''',
            (key({'supi': body.imsi}), str(body.session_id), body.sequence, now, now, json.dumps(payload)))
    _publish_p1203(body)
    return {'status': 'recorded', 'app_id': 'stream5g'}


def latest(supi: str, app_id: str):
    if app_id != 'stream5g':
        return None
    with transaction() as db:
        row = db.execute('''SELECT * FROM terminal_playback WHERE terminal_key=?
            ORDER BY received_at DESC, sequence DESC LIMIT 1''', (key({'supi': supi}),)).fetchone()
    if row is None:
        return None
    return {**json.loads(row['payload']), 'source': 'browser-player',
            'received_at': row['received_at'], 'fresh': time.time()-row['received_at'] <= 180}
