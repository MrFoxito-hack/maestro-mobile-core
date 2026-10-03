"""Bounded, restart-safe replay of the PCF durable journal, using event IDs."""
import json
import os
from pathlib import Path
import time
import urllib.request


def replay(journal, checkpoint, token, *, maximum=64, deadline_seconds=10):
    start=time.monotonic()
    identity=journal.stat()
    position=0
    if checkpoint.exists():
        previous=json.loads(checkpoint.read_text())
        if previous['inode']==identity.st_ino and previous['device']==identity.st_dev:
            position=previous['offset']
            if position>identity.st_size:
                raise ValueError('Append-only journal was truncated')
    count=0
    with journal.open('rb') as source:
        source.seek(position)
        while count<maximum and time.monotonic()-start<deadline_seconds:
            raw=source.readline()
            if not raw or not raw.endswith(b'\n'):
                break  # concurrent partial append is retried on the next tick
            body=json.loads(raw)
            if not isinstance(body,dict) or 'event_id' not in body:
                raise ValueError('Invalid journal record')
            request=urllib.request.Request('http://127.0.0.1:8085/management/v1/closed-loop/events',
                data=raw,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
            # Loopback must never follow redirects carrying the credential.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):
                    return None
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)
            with opener.open(request,timeout=2) as response:
                if response.status!=201:
                    raise RuntimeError('Ledger did not accept replay')
            state={'inode':identity.st_ino,'device':identity.st_dev,'offset':source.tell()}
            temporary=checkpoint.with_suffix('.tmp')
            with temporary.open('w') as target:
                json.dump(state,target);target.flush();os.fsync(target.fileno())
            os.replace(temporary,checkpoint)
            count+=1
    return count


if __name__=='__main__':
    print(json.dumps({'replayed':replay(Path(os.environ['MAESTRO_NWDAF_AUDIT_FILE']),
        Path(os.environ['MAESTRO_NWDAF_REPLAY_CHECKPOINT']),
        Path(os.environ['MAESTRO_NWDAF_TOKEN_FILE']).read_text().strip())}))
