"""Exercise the compiled SMF Nchf client against this real CHF over TCP HTTP/2.

Component acceptance only: test SUPI/database, no UE traffic and no N4. The
probe's reservation is reconciled after its process exits, never called Release.
All spawned processes are terminated; production NFs are not restarted.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import ChargingDataRequest
from app.repository import ChargingRepository
from app.service import ChargingService
from tools.contract_validation import validator

OWNER = "00100000-0000-4000-8000-000000000001"
SUPI = "imsi-001010000000001"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def reconcile_stopped_probes(repository):
    with repository.transaction() as conn:
        sessions = conn.execute("SELECT charging_data_ref FROM charging_sessions WHERE status='OPEN'").fetchall()
    for session in sessions:
        ChargingService(repository, 1048576, 300).reconcile(
            session[0], "Native component probe exited; no N4 session created")


def main():
    request_contract = validator("ChargingDataRequest")
    response_contract = validator("ChargingDataResponse")
    probe = ROOT.parent / "open5gs/build/src/smf/chf-probe"
    if not probe.is_file():
        raise SystemExit("Build src/smf/chf-probe in the adjacent pinned source first")
    evidence_root = ROOT.parent / ".work"
    evidence_root.mkdir(exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="native-create-", dir=evidence_root))
    token, admin_token = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    port = free_port()
    env = os.environ | {
        "CHF_DATABASE_PATH": str(evidence / "charging.sqlite3"),
        "CHF_SBI_TOKENS": json.dumps({OWNER: token}),
        "CHF_ADMIN_TOKEN": admin_token,
        "CHF_SBI_LAB_NO_AUTH": "false",
        "SMF_CHF_TOKEN": token,
    }
    repository = ChargingRepository(Path(env["CHF_DATABASE_PATH"]))
    repository.initialize()
    repository.upsert_account(SUPI, 8 * 1048576, True, actor="native-component-fixture")
    config = {
        "logger": {"level": "error"},
        "global": {"max": {"ue": 16}},
        "smf": {"sbi": {"server": [{"address": "127.0.0.254", "port": 18084}]},
                "chf": {"enabled": True, "sbi": [{"addr": "127.0.0.1", "port": port}],
                        "journal_dir": str(evidence / "native-journal"),
                        "nf_instance_id": OWNER, "timeout_ms": 500, "retries": 2}},
    }
    config_path = evidence / "probe.yaml"
    # JSON is also valid YAML; never touches /etc/open5gs.
    config_path.write_text(json.dumps(config))
    results = {}
    with (evidence / "http.log").open("w") as log:
        server = subprocess.Popen([
            sys.executable, "-m", "hypercorn", "app.main:app", "--bind", f"127.0.0.1:{port}",
            "--access-logfile", "-", "--access-logformat", "%(H)s %(s)s %(r)s",
        ], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"CHF exited; inspect {evidence / 'http.log'}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise TimeoutError("CHF readiness deadline exceeded")
            cases = [
                ("authorized", "success", env, 8 * 1048576, True, 1048576, 201, False),
                ("wrong_credential", "failure", env | {"SMF_CHF_TOKEN": secrets.token_urlsafe(40)},
                 8 * 1048576, True, 0, 403, False),
                ("positive_final_grant", "success", env, 524288, True, 524288, 201, True),
                ("no_available_credit_denied", "failure", env, 1048576, True, 0, 201, True),
                ("disabled_account_denied", "failure", env, 1048576, False, 0, 403, False),
            ]
            for name, expectation, case_env, quota, enabled, grant, status, final in cases:
                repository.upsert_account(SUPI, quota, enabled, actor="native-component-fixture")
                held = 0
                if name == "no_available_credit_denied":
                    # Reserve all credit through another real native Create. No invented usage.
                    holder = subprocess.run([str(probe), str(config_path), "success"], env=env,
                                            text=True, capture_output=True, timeout=40)
                    (evidence / "credit_holder.log").write_text(holder.stdout + holder.stderr)
                    assert holder.returncode == 0, holder.stderr
                    held = repository.get_account(SUPI)["reserved_bytes"]
                    assert held == quota
                completed = subprocess.run([str(probe), str(config_path), expectation],
                                           env=case_env, text=True, capture_output=True, timeout=40)
                (evidence / f"{name}.log").write_text(completed.stdout + completed.stderr)
                if completed.returncode:
                    raise RuntimeError(f"Native probe {name} failed: {completed.returncode}; {evidence}")
                assert f"NCHF_HTTP_STATUS={status}" in completed.stdout, completed.stdout
                assert f"NCHF_GRANT={grant}\n" in completed.stdout, completed.stdout
                assert f"NCHF_FINAL={int(final)}\n" in completed.stdout, completed.stdout
                assert "NCHF_RETRIES=0\n" in completed.stdout, completed.stdout
                account = repository.get_account(SUPI)
                assert account["reserved_bytes"] == grant + held and account["consumed_bytes"] == 0, account
                results[name] = "PASS"
                if name == "authorized":
                    line = next(line for line in completed.stdout.splitlines()
                                if line.startswith("NCHF_REQUEST_JSON="))
                    payload = json.loads(line.split("=", 1)[1])
                    ChargingDataRequest.model_validate(payload)
                    request_contract.validate(payload)
                    response_line = next(line for line in completed.stdout.splitlines()
                                         if line.startswith("NCHF_RESPONSE_JSON="))
                    response_contract.validate(json.loads(response_line.split("=", 1)[1]))
                    assert payload["nfConsumerIdentification"]["nFName"] == OWNER
                    assert payload["pDUSessionChargingInformation"]["pduSessionInformation"]["dnnId"] == "internet"
                    (evidence / "native-create-request.json").write_text(json.dumps(payload, indent=2))
                reconcile_stopped_probes(repository)
            repository.upsert_account(SUPI, 8 * 1048576, True, actor="native-component-fixture")
            lifecycle = subprocess.run([str(probe), str(config_path), "lifecycle"], env=env,
                                       text=True, capture_output=True, timeout=40)
            (evidence / "lifecycle.log").write_text(lifecycle.stdout + lifecycle.stderr)
            assert lifecycle.returncode == 0, lifecycle.stdout + lifecycle.stderr
            assert "NCHF_DUPLICATE_LOCAL=REJECTED" in lifecycle.stdout
            assert [line for line in lifecycle.stdout.splitlines() if line.startswith("NCHF_HTTP_STATUS=")] == [
                "NCHF_HTTP_STATUS=201", "NCHF_HTTP_STATUS=200", "NCHF_HTTP_STATUS=204"]
            for line in lifecycle.stdout.splitlines():
                if line.startswith("NCHF_REQUEST_JSON="):
                    ChargingDataRequest.model_validate_json(line.split("=", 1)[1])
                    request_contract.validate(json.loads(line.split("=", 1)[1]))
                if line.startswith("NCHF_RESPONSE_JSON="):
                    response_contract.validate(json.loads(line.split("=", 1)[1]))
            account = repository.get_account(SUPI)
            assert account["consumed_bytes"] == 400 and account["reserved_bytes"] == 0, account
            with repository.transaction() as conn:
                records = conn.execute("SELECT record_json FROM charging_cdrs").fetchall()
            cdrs = [json.loads(row[0]) for row in records]
            native_cdr = next(cdr for cdr in cdrs if cdr["evidence"] == "NF_REPORTED")
            assert native_cdr["totalBytes"] == 400
            assert native_cdr["uplinkBytes"] == 150 and native_cdr["downlinkBytes"] == 250
            assert native_cdr["terminationReason"] == "FINAL"
            assert native_cdr["sumOfReplacementGrantsBytes"] == 2 * 1048576
            (evidence / "component-fixture-cdr.json").write_text(json.dumps(native_cdr, indent=2))
            results["native_update_release_fixture_not_ue_traffic"] = "PASS"
            results["native_messages_official_rel16_openapi"] = "PASS"
            # Real refused TCP connection, not a fake responder or simulated delay.
            config["smf"]["chf"]["sbi"][0]["port"] = free_port()
            config_path.write_text(json.dumps(config))
            unavailable = subprocess.run([str(probe), str(config_path), "failure"], env=env,
                                         text=True, capture_output=True, timeout=15)
            (evidence / "unavailable.log").write_text(unavailable.stdout + unavailable.stderr)
            assert unavailable.returncode == 0, unavailable.stderr
            assert "NCHF_RETRIES=2\n" in unavailable.stdout, unavailable.stdout
            assert "NCHF_GRANT=0\n" in unavailable.stdout, unavailable.stdout
            results["unavailable_bounded_retries"] = "PASS"
            config["smf"]["chf"]["enabled"] = False
            config["smf"]["chf"]["sbi"] = "intentionally-invalid-ignored"
            config_path.write_text(json.dumps(config))
            disabled = subprocess.run([str(probe), str(config_path), "disabled"], env=env,
                                      text=True, capture_output=True, timeout=15)
            (evidence / "disabled.log").write_text(disabled.stdout + disabled.stderr)
            assert disabled.returncode == 0, disabled.stderr
            results["disabled_no_chf_io"] = "PASS"
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    http_log = (evidence / "http.log").read_text()
    assert "2 201" in http_log and "2 403" in http_log, http_log
    results["tcp_http2_observed"] = "PASS"
    # This is explicit reconciliation of a STOPPED test consumer, not a fabricated native Release.
    reconcile_stopped_probes(repository)
    results["reserved_bytes_after_stopped_probe_reconciliation"] = repository.get_account(SUPI)["reserved_bytes"]
    (evidence / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({"results": results, "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
