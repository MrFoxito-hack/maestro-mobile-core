"""Only the sandbox adapter is shipped. No SSH, MML, subprocess or Core imports."""

from typing import Protocol

from app.laboratory.repository import ConflictError
from app.laboratory.runtime import Runtime


class Cancelled(Exception):
    pass


class Adapter(Protocol):
    def perform(self, execution: dict, step: dict) -> dict: ...


class SandboxAdapter:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def perform(self, execution, step):
        with self.runtime.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.runtime.fenced(db, execution["id"], execution["token"])
            action = step["action"]
            restoring = action in ("restore", "verify_restored")
            grant = db.execute("SELECT * FROM lab_assignments WHERE id=?", (row["assignment_id"],)).fetchone()
            if not restoring and (row["cancel_requested"] or not grant["enabled"] or grant["expires_at"] <= self.runtime.clock()):
                raise Cancelled()
            resource = self.runtime.resource(row["testbed"])
            state = db.execute("SELECT value FROM lab_sandbox WHERE resource=?", (resource,)).fetchone()
            if state is None:
                raise ConflictError("Falta el recurso simulado; recuperación no verificable.")
            current = state[0]
            if action in ("prepare", "verify_restored") and current != row["baseline"]:
                raise ConflictError("El estado de ensayo no coincide con su checkpoint.")
            if action == "apply":
                db.execute("UPDATE lab_sandbox SET value=? WHERE resource=?", (step["run"]["treatment"], resource))
            elif action == "verify":
                if current != step["run"]["treatment"]:
                    raise ConflictError("La intervención simulada no fue verificada.")
            elif action == "restore":
                db.execute("UPDATE lab_sandbox SET value=? WHERE resource=?", (row["baseline"], resource))
            elif action not in ("preflight", "prepare", "measure", "verify_restored"):
                raise ValueError("Acción fuera del catálogo de ensayo.")
            return {"action": action, "run_ordinal": step["run"]["ordinal"] if step["run"] else None,
                    "source": "dry_run", "network_measurements": False,
                    "metrics": None, "adapter": "sqlite-sandbox-v1"}
