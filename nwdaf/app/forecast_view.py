"""Keep short UPF observations separate from the model's training history."""
from datetime import datetime, timezone
import json


def predictions(database):
    now = datetime.now(timezone.utc).timestamp()

    def representation(row):
        payload = json.loads(row['sbi_payload']) if row['sbi_payload'] else None
        return {'created': row['created'], 'input_hash': row['input_hash'],
                'evidence': json.loads(row['evidence']),
                'fresh': bool(payload and datetime.fromisoformat(payload['expiry']).timestamp() > now)}

    items = []
    with database.connect() as db:
        targets = db.execute("SELECT DISTINCT target FROM analyses WHERE event='LOAD_LEVEL_INFORMATION' ORDER BY target LIMIT 100").fetchall()
        for target_row in targets:
            target = target_row['target']
            # A newer one-second measurement must never replace a forecast.
            model = db.execute("""SELECT * FROM analyses
                WHERE event='LOAD_LEVEL_INFORMATION' AND target=?
                AND json_extract(evidence, '$.model') != 'upf-counter-delta-v1'
                ORDER BY created DESC,id DESC LIMIT 1""", (target,)).fetchone()
            live = db.execute("""SELECT * FROM analyses
                WHERE event='LOAD_LEVEL_INFORMATION' AND target=? AND created>=?
                AND json_extract(evidence, '$.model') = 'upf-counter-delta-v1'
                ORDER BY created DESC,id DESC LIMIT 120""", (target, now-600)).fetchall()
            if model is None and not live:
                continue
            item = {'snssai': json.loads(target), **representation(model or live[0])}
            if model is None:
                item['evidence'] = {'status': 'insufficient_history', 'history': [], 'points': []}
            if live:
                observation = representation(live[0])
                observation['evidence']['history'] = [
                    point for row in reversed(live)
                    for point in json.loads(row['evidence']).get('history', [])]
                item['observation'] = observation
            items.append(item)
    return items
