from collections import Counter
from threading import Lock


class RequestMetrics:
    """Process-local request metrics. Durable charging gauges come from SQLite."""
    def __init__(self):
        self.lock = Lock()
        self.requests, self.errors, self.seconds = Counter(), Counter(), Counter()

    def observe(self, operation, status, elapsed):
        with self.lock:
            self.requests[operation] += 1
            self.errors[operation] += int(status >= 400)
            self.seconds[operation] += elapsed

    def render(self, repository, stale_before):
        with self.lock:
            lines = []
            for name, values in [('chf_requests_total', self.requests),
                                 ('chf_request_errors_total', self.errors),
                                 ('chf_request_latency_seconds_sum', self.seconds),
                                 ('chf_request_latency_seconds_count', self.requests)]:
                for operation, value in sorted(values.items()):
                    lines.append(f'{name}{{operation="{operation}"}} {value}')
        with repository.transaction() as conn:
            rows = {row['status']: row['n'] for row in conn.execute('SELECT status,COUNT(*) AS n FROM charging_sessions GROUP BY status')}
            usage = conn.execute('SELECT COALESCE(SUM(observed_bytes),0),COALESCE(SUM(overrun_bytes),0),COALESCE(SUM(reserved_bytes),0) FROM charging_sessions').fetchone()
            stale = conn.execute("SELECT COUNT(*) FROM charging_sessions WHERE status='OPEN' AND (valid_until < ? OR owner_nf IS NULL)", (stale_before,)).fetchone()[0]
            reservations = conn.execute("SELECT COUNT(*) FROM charging_sessions WHERE status='OPEN' AND reserved_bytes>0").fetchone()[0]
            replays = conn.execute('SELECT COUNT(*) FROM charging_replays').fetchone()[0]
            grants = conn.execute('SELECT COALESCE(SUM(granted_bytes),0) FROM charging_events').fetchone()[0]
            exhausted = conn.execute("SELECT COUNT(*) FROM charging_events WHERE result_code='QUOTA_LIMIT_REACHED'").fetchone()[0]
        for name, value in {'charging_sessions_active': rows.get('OPEN', 0),
                            'charging_sessions_created_total': sum(rows.values()),
                            'charging_sessions_released_total': rows.get('RELEASED', 0),
                            'charging_usage_bytes': usage[0], 'charging_overrun_bytes': usage[1],
                            'charging_reservations_bytes': usage[2],
                            'charging_reservations_active': reservations,
                            'charging_idempotent_requests_total': replays,
                            'charging_quota_granted_bytes': grants,
                            'charging_quota_exhausted_total': exhausted,
                            'charging_reservations_orphaned': stale}.items():
            lines.append(f'{name} {value}')
        return '\n'.join(lines) + '\n'
