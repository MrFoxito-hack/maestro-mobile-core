from uuid import uuid4

import pytest

from app.services.telco_kpis import parse_telco_logs, telco_kpi_repository
from app.services.performance import COUNTER_BY_ID, compatible, MetricsCollector


def test_registration_success_marker_is_not_counted_twice():
    events, _ = parse_telco_logs('5g-sa', 'ue', [
        '[2026-09-26 12:00:00.000] Sending Initial Registration',
        '[2026-09-26 12:00:00.100] Registration accept received',
        '[2026-09-26 12:00:00.101] Initial Registration is successful',
    ])
    assert [e['event_type'] for e in events] == ['attempt', 'success']


@pytest.mark.parametrize('attempts,successes,rejects,expected', [
    (2, 1, 1, 50.0), (1, 1, 0, 100.0), (0, 1, 0, None),
    (1, 2, 0, None), (1, 1, 1, None), (1, 0, 0, 0.0),
])
def test_rate_never_fabricates_success(client, attempts, successes, rejects, expected):
    lines = []
    for phrase, count in [('Sending Initial Registration', attempts),
                          ('Initial Registration is successful', successes),
                          ('Registration rejected', rejects)]:
        for _ in range(count):
            lines.append(f'[2026-09-26 12:00:{len(lines):02d}.000] {phrase}')
    key = f'test-rate-{uuid4()}'
    events, _ = parse_telco_logs('5g-sa', key, lines)
    telco_kpi_repository.insert_events(key, '5g-sa', events)
    summary = telco_kpi_repository.summary(key, '5g-sa')['registration']
    assert summary.get('success_rate') == expected
    values = MetricsCollector._telco_values('5g-sa', {'summary': {'registration': summary}})
    rates = [v for v in values if v[1] == '5g.registration.success_rate']
    assert len(rates) == (0 if expected is None else 1)


def test_global_logs_are_not_assigned_to_an_nf():
    counter = COUNTER_BY_ID['5g.registration.success_rate']
    assert compatible(counter, 'procedure:registration')
    assert not compatible(counter, 'nf:amf')
