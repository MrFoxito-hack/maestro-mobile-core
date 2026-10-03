"""Run separately: python -m app.laboratory.worker [--once | --watchdog-only]."""

import argparse
import json
import logging
from pathlib import Path
import signal
import threading
import uuid

from app.laboratory.adapters import Cancelled, SandboxAdapter
from app.laboratory.runtime import LeaseLost, Runtime
from app.laboratory.storage import repository

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, runtime, adapter=None):
        self.runtime = runtime
        self.id = uuid.uuid4().hex
        self.adapter = adapter or SandboxAdapter(runtime)
        self.real_adapter = None

    def adapter_for(self, row):
        if row['mode'] == 'real':
            if self.real_adapter is None:
                from app.laboratory.qoe_adapter import QoEClosedLoopAdapter
                self.real_adapter = QoEClosedLoopAdapter(self.runtime)
            return self.real_adapter
        return self.adapter

    def perform(self, row, step):
        adapter = self.adapter_for(row)
        if row['mode'] != 'real':
            return adapter.perform(row, step)
        stop = threading.Event()
        failures = []
        def renew():
            while not stop.wait(3):
                try:
                    self.runtime.heartbeat(row['id'], row['token'])
                except Exception as error:
                    failures.append(error)
                    break
        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            result = adapter.perform(row, step)
            if failures:
                raise failures[0]
            return result
        finally:
            stop.set()
            thread.join(timeout=12)

    def export_real(self, row):
        if row['mode'] == 'real':
            self.adapter_for(row).export(row)

    def recovery(self, row, final_status):
        self.runtime.begin_recovery(row["id"], row["token"])
        for action in ("restore", "verify_restored"):
            step = {"action": action, "run": None, "recovery": True}
            self.runtime.heartbeat(row["id"], row["token"])
            self.runtime.intent(row["id"], row["token"], step)
            result = self.perform(row, step)
            self.runtime.result(row["id"], row["token"], result)
        if row['mode']=='real':self.adapter_for(row).finalize(row)
        self.runtime.finish(row["id"], row["token"], final_status)
        self.export_real(row)

    def tick(self, recovery_only=False):
        self.runtime.expire()
        try:
            row = self.runtime.active(self.id) or self.runtime.claim_recovery(self.id)
            if row is None and not recovery_only:
                row = self.runtime.claim(self.id)
        except LeaseLost:
            # Lease may expire between the watchdog pass and the atomic claim.
            return True
        if row is None:
            return False
        try:
            cancelled = self.runtime.heartbeat(row["id"], row["token"])
            if row["status"] == "recovering" or cancelled:
                try:
                    self.recovery(row, "cancelled" if cancelled else "failed")
                except LeaseLost:
                    raise
                except Exception:
                    self.runtime.require_recovery(row["id"], row["token"], "recovery_failed")
                return True
            steps = self.runtime.work(row)
            if row["cursor"] >= len(steps):
                if row['mode']=='real':self.adapter_for(row).finalize(row)
                self.runtime.finish(row["id"], row["token"], "completed")
                self.export_real(row)
                return True
            step = steps[row["cursor"]]
            # Write intent before effect; a crash never replays an uncertain effect.
            self.runtime.intent(row["id"], row["token"], step)
            result = self.perform(row, step)
            self.runtime.result(row["id"], row["token"], result)
            self.export_real(row)
        except LeaseLost:
            log.warning("Worker lost ownership of execution %s", row["id"])
        except Exception as exc:
            # Persist a bounded error code, not credentials or an adapter traceback.
            try:
                from app.laboratory.qoe_adapter import RealPreflightBlocked
                code = str(exc) if isinstance(exc, RealPreflightBlocked) else 'cancelled' if isinstance(exc, Cancelled) else 'step_failed'
                self.runtime.require_recovery(row["id"], row["token"], code)
                self.export_real(row)
            except LeaseLost:
                log.warning("Ownership expired while handling execution %s", row["id"])
        return True

    def stop(self):
        row = self.runtime.active(self.id)
        if row:
            try:
                self.runtime.require_recovery(row["id"], row["token"], "worker_stopped")
            except LeaseLost:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Drain currently available authorized jobs and exit.")
    parser.add_argument("--watchdog-only", action="store_true", help="Expire stale owners and recover SQLite sandbox jobs; never claim queued campaigns.")
    parser.add_argument("--stop-file", type=Path, help="Local launcher shutdown signal (use a unique path per process).")
    parser.add_argument("--check-real", action="store_true", help="Read real sessions, quota and PCF status; write readiness evidence and exit without claiming jobs.")
    parser.add_argument("--competing-index", type=int, choices=range(2, 7), default=4)
    args = parser.parse_args()
    if args.check_real:
        if args.once or args.watchdog_only or args.stop_file:
            parser.error('--check-real is separate from queue execution')
        from app.laboratory.real_readiness import run_check
        raise SystemExit(run_check(args.competing_index))
    store = repository()
    store.initialize()
    runtime, stop = Runtime(store), threading.Event()
    worker = Worker(runtime)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    print(json.dumps({"worker": "laboratory", "mode": "assignment_scoped", "real_adapter": "qoe_closed_loop",
                      "watchdog_only": args.watchdog_only}), flush=True)
    try:
        while not stop.is_set() and not (args.stop_file and args.stop_file.exists()):
            worked = worker.tick(recovery_only=args.watchdog_only)
            if args.once and not worked:
                break
            stop.wait(0.1 if args.once else 0.25)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
