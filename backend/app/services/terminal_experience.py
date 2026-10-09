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
    """Aggregate telemetry cannot reconstruct audiovisual metadata or stall times.

    Keep browser observations available, but never publish an invented P.1203
    document. Laboratory scoring uses qoe_metrics with probed media and the
    complete player trace; it is deliberately independent of live policy.
    """
    return None


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
