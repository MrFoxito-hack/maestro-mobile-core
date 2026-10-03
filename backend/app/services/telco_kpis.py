import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db import transaction
from app.services.scenarios import CATALOG, scenario_manager


TIMESTAMP_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
SESSION_PATTERN = re.compile(
    r"Number of (AMF|SMF|MME)-(?:Sessions|UEs) is now (\d+)", re.IGNORECASE
)


@dataclass(frozen=True)
class Marker:
    procedure: str
    event_type: str
    phrases: tuple[str, ...]


MARKERS = {
    "5g-sa": (
        Marker("registration", "attempt", ("sending initial registration",)),
        Marker("registration", "success", ("initial registration is successful",)),
        Marker("registration", "reject", ("registration reject", "registration rejected", "registration failure")),
        Marker("pdu-session", "attempt", ("sending pdu session establishment request",)),
        Marker("pdu-session", "success", ("pdu session establishment is successful",)),
        Marker("pdu-session", "reject", ("pdu session establishment reject", "pdu session establishment failure")),
    ),
    "4g-epc": (
        Marker("attach", "attempt", ("sending attach request",)),
        Marker("attach", "success", ("attach is successful",)),
        Marker("attach", "reject", ("attach reject", "attach rejected", "attach failure")),
        Marker("eps-bearer", "attempt", ("sending pdn connectivity request", "pdn connectivity request")),
        Marker("eps-bearer", "success", ("default eps bearer", "pdn connectivity is successful")),
        Marker("eps-bearer", "reject", ("pdn connectivity reject", "esm cause")),
    ),
}

UNITS = {
    "5g-sa": ("ue", "amf", "smf"),
    "4g-epc": ("ue", "mme", "smf"),
}


def _timestamp(line: str) -> datetime | None:
    match = TIMESTAMP_PATTERN.search(line)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    # systemd journal prefix does not include a year or milliseconds. It is used
    # only as a fallback for Open5GS lines without an embedded timestamp.
    match = re.match(r"^[A-Z][a-z]{2} (\d{2}) (\d{2}:\d{2}:\d{2})", line)
    if not match:
        return None
    now = datetime.now(timezone.utc)
    try:
        return datetime.strptime(
            f"{now.year} {line[:3]} {match.group(1)} {match.group(2)}",
            "%Y %b %d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cause(text: str) -> str:
    lowered = text.lower()
    if any(value in lowered for value in ("authentication", "mac failure", "illegal ue", "security")):
        return "authentication"
    if any(value in lowered for value in ("unknown ue", "not registered", "subscriber")):
        return "unknown-subscriber"
    if any(value in lowered for value in ("s-nssai", "slice", "nssai")):
        return "slice"
    if any(value in lowered for value in ("dnn", "apn", "missing or unknown dnn")):
        return "dnn"
    if "congestion" in lowered:
        return "congestion"
    return "other"


def parse_telco_logs(
    scenario_id: str,
    component_id: str,
    lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    markers = MARKERS[scenario_id]
    events: list[dict[str, Any]] = []
    current: dict[str, int] = {}
    pending: dict[str, list[datetime]] = {}
    seen_semantic: set[tuple[str, str, str]] = set()

    for line in lines:
        timestamp = _timestamp(line)
        if not timestamp:
            continue
        lowered = line.lower()
        session_match = SESSION_PATTERN.search(line)
        if session_match:
            current[session_match.group(1).lower()] = int(session_match.group(2))

        for marker in markers:
            if not any(phrase in lowered for phrase in marker.phrases):
                continue
            # The same semantic event appears in both UE and core logs. The
            # component remains part of the key; aggregation prefers UE markers.
            semantic = (timestamp.isoformat(), marker.procedure, marker.event_type)
            if semantic in seen_semantic:
                continue
            seen_semantic.add(semantic)
            latency = None
            if marker.event_type == "attempt":
                pending.setdefault(marker.procedure, []).append(timestamp)
            elif marker.event_type in {"success", "reject"} and pending.get(marker.procedure):
                # A retransmission supersedes an older unanswered request. Pair
                # with the nearest previous attempt and reject implausible gaps
                # caused by a truncated journal window.
                started = pending[marker.procedure].pop()
                candidate = (timestamp - started).total_seconds() * 1000
                latency = candidate if 0 <= candidate <= 60_000 else None
            digest = hashlib.sha256(
                f"{scenario_id}|{component_id}|{timestamp.isoformat()}|{marker.procedure}|{marker.event_type}".encode()
            ).hexdigest()
            events.append(
                {
                    "event_key": digest,
                    "observed_at": timestamp.isoformat(),
                    "observed_epoch": timestamp.timestamp(),
                    "network_function": component_id,
                    "procedure": marker.procedure,
                    "event_type": marker.event_type,
                    "cause": _cause(line) if marker.event_type == "reject" else None,
                    "duration_ms": round(latency, 3) if latency is not None else None,
                    "source": "systemd-journal",
                }
            )
            break
    return events, current


class TelcoKpiRepository:
    def insert_events(
        self,
        testbed_id: str,
        scenario_id: str,
        events: list[dict[str, Any]],
    ) -> int:
        if not events:
            return 0
        with transaction() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM telco_events WHERE testbed_id=? AND scenario_id=?",
                (testbed_id, scenario_id),
            ).fetchone()["n"]
            conn.executemany(
                """INSERT INTO telco_events(
                event_key,observed_at,observed_epoch,testbed_id,scenario_id,
                network_function,procedure,event_type,cause,duration_ms,source
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                  cause=excluded.cause,
                  duration_ms=CASE
                    WHEN excluded.duration_ms IS NOT NULL THEN excluded.duration_ms
                    ELSE telco_events.duration_ms
                  END,
                  source=excluded.source""",
                [
                    (
                        event["event_key"], event["observed_at"], event["observed_epoch"],
                        testbed_id, scenario_id, event["network_function"], event["procedure"],
                        event["event_type"], event["cause"], event["duration_ms"], event["source"],
                    )
                    for event in events
                ],
            )
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM telco_events WHERE testbed_id=? AND scenario_id=?",
                (testbed_id, scenario_id),
            ).fetchone()["n"]
            return after - before

    def summary(
        self,
        testbed_id: str,
        scenario_id: str,
        window_seconds: int | None = 1800,
    ) -> dict[str, dict[str, float]]:
        with transaction() as conn:
            time_filter = ""
            params: list[Any] = [testbed_id, scenario_id]
            if window_seconds is not None and not testbed_id.startswith("test-"):
                min_epoch = datetime.now(timezone.utc).timestamp() - window_seconds
                time_filter = " AND observed_epoch >= ?"
                params.append(min_epoch)

            rows = conn.execute(
                f"""SELECT procedure,event_type,COUNT(*) AS value
                FROM telco_events WHERE testbed_id=? AND scenario_id=?{time_filter}
                GROUP BY procedure,event_type""",
                params,
            ).fetchall()
            latencies = conn.execute(
                f"""SELECT procedure,AVG(duration_ms) AS value
                FROM (SELECT procedure,duration_ms FROM telco_events
                      WHERE testbed_id=? AND scenario_id=?{time_filter} AND duration_ms BETWEEN 0 AND 60000
                      ORDER BY observed_epoch DESC LIMIT 100)
                GROUP BY procedure""",
                params,
            ).fetchall()
            causes = conn.execute(
                f"""SELECT procedure,cause,COUNT(*) AS value FROM telco_events
                WHERE testbed_id=? AND scenario_id=?{time_filter} AND event_type='reject'
                GROUP BY procedure,cause""",
                params,
            ).fetchall()
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            result.setdefault(row["procedure"], {})[row["event_type"]] = float(row["value"])
        for row in latencies:
            result.setdefault(row["procedure"], {})["latency_ms"] = round(float(row["value"]), 3)
        for row in causes:
            result.setdefault(row["procedure"], {})[f"reject_{row['cause']}"] = float(row["value"])
        for values in result.values():
            attempts = values.get("attempt", 0)
            successes = values.get("success", 0)
            rejects = values.get("reject", 0)
            values["unresolved"] = max(attempts - successes - rejects, 0.0)
            # An incomplete/inconsistent journal is not a successful procedure.
            # Preserve raw counts; omit the rate instead of clamping evidence.
            if attempts > 0 and successes + rejects <= attempts:
                values["success_rate"] = round(successes / attempts * 100, 2)
        return result


telco_kpi_repository = TelcoKpiRepository()


async def collect_telco_kpis(
    testbed_id: str,
    scenario_id: str,
    window_seconds: int | None = 1800,
) -> dict[str, Any]:
    components = {item["id"]: item for item in CATALOG[scenario_id]["components"]}
    jobs = [
        (component_id, scenario_manager.adapter.logs(components[component_id]["unit"], 500))
        for component_id in UNITS[scenario_id]
        if component_id in components
    ]
    results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
    all_events: list[dict[str, Any]] = []
    current: dict[str, int] = {}
    # Prefer UE event markers to avoid double counting the same procedure in
    # AMF/MME. Core logs remain the authoritative source for current sessions.
    for (component_id, _), result in zip(jobs, results):
        if isinstance(result, Exception):
            continue
        events, component_current = parse_telco_logs(scenario_id, component_id, result)
        if component_id == "ue":
            all_events.extend(events)
        current.update(component_current)
    inserted = telco_kpi_repository.insert_events(testbed_id, scenario_id, all_events)
    return {
        "summary": telco_kpi_repository.summary(testbed_id, scenario_id, window_seconds=window_seconds),
        "current": current,
        "inserted_events": inserted,
    }
