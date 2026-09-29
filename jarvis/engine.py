"""Plans are short-lived, single-use approvals; receipts describe actual outcomes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .actions import ActionExecutor
from .planner import Planner, PlanError


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Engine:
    def __init__(self, planner: Planner, executor: ActionExecutor, state_dir: Path):
        self.planner, self.executor, self.state_dir = planner, executor, state_dir
        self.plans: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.journal = self.state_dir / "history.json"
        if self.journal.is_symlink():
            raise PlanError("The history file cannot be a symlink.")
        self.history = []
        if self.journal.exists():
            try:
                data = json.loads(self.journal.read_text())
                if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                    raise ValueError("Invalid history")
                self.history = data[:100]
                for receipt in self.history:
                    if receipt.get("status") == "running":
                        receipt["status"] = "interrupted"
                        receipt["notice"] = "Execution was interrupted. Check the affected app before trying again."
            except (OSError, ValueError) as exc:
                raise PlanError("Cannot read local history. Preserve or rename history.json before restarting.") from exc

    def _save(self) -> None:
        path = self.state_dir / (".history-" + uuid.uuid4().hex + ".tmp")
        try:
            with path.open("x", encoding="utf-8") as file:
                os.chmod(path, 0o600)
                json.dump(self.history[:100], file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            path.replace(self.journal)
        finally:
            path.unlink(missing_ok=True)

    def plan(self, text: str) -> dict:
        raw, decision = self.planner.plan(text)
        actions = self.executor.validate(raw)
        title = actions[0]["title"] if len(actions) == 1 else f"{len(actions)} actions"
        plan = {"id": uuid.uuid4().hex, "text": text, "title": title, "actions": actions,
                "created_at": stamp(), "expires_at": time.time() + 300, **decision}
        with self.lock:
            self.plans = {k: p for k, p in self.plans.items() if p["expires_at"] > time.time()}
            if len(self.plans) >= 50:
                raise PlanError("Too many pending plans. Cancel a preview before creating another.")
            self.plans[plan["id"]] = deepcopy(plan)
        return plan

    def cancel(self, plan_id: str) -> dict:
        with self.lock:
            self.plans.pop(plan_id, None)
        return {"ok": True}

    def execute(self, plan_id: str) -> dict:
        with self.lock:
            plan = self.plans.pop(plan_id, None)
            if plan is None:
                raise PlanError("This plan was already used or cancelled. Create a fresh preview.")
            if plan["expires_at"] <= time.time():
                raise PlanError("This preview expired. Create a fresh plan before running it.")
            # Never accept action arguments from a confirmation request.
            receipt = {"id": uuid.uuid4().hex, "plan_id": plan_id, "text": plan["title"],
                       "status": "running", "created_at": stamp(), "steps": [],
                       "provider": plan["provider"], "model": plan["model"], "can_undo": False}
            self.history.insert(0, receipt)
            self.history = self.history[:100]
            self._save()  # Fail before an effect if the activity store isn't writable.
            for action in plan["actions"]:
                try:
                    result = self.executor.execute(action)
                except Exception as exc:
                    # Native adapter exceptions use safe messages; never expose tracebacks/keys.
                    from .actions import ActionError
                    summary = str(exc) if isinstance(exc, ActionError) else "Step failed. Check the app before retrying."
                    result = {"status": "failed", "summary": summary}
                receipt["steps"].append({"title": action["title"], "kind": action["kind"], **result})
                if result["status"] == "failed":
                    break
                self._save()
            failed = any(s["status"] == "failed" for s in receipt["steps"])
            if failed:
                receipt["status"] = "partial" if any(s["status"] in {"done", "simulated"} for s in receipt["steps"]) else "failed"
                receipt["notice"] = "Stopped at the failed step. Later steps were not run."
            else:
                receipt["status"] = "done" if self.executor.live else "simulated"
            receipt["can_undo"] = any(s.get("undo") for s in receipt["steps"])
            self._save()
            return self._public(receipt)

    def undo(self, receipt_id: str) -> dict:
        with self.lock:
            if not self.executor.live:
                raise PlanError("Restart in live mode to undo a previous live action.")
            receipt = next((r for r in self.history if r["id"] == receipt_id), None)
            if not receipt or not receipt.get("can_undo"):
                raise PlanError("There are no reversible steps in this receipt.")
            for step in reversed(receipt["steps"]):
                if step.get("undo"):
                    result = self.executor.undo(step["undo"])
                    step["undo_result"] = result["summary"]
                    step.pop("undo")
                    step["status"] = "undone"
                    self._save()
            receipt["can_undo"] = False
            receipt["notice"] = "Reversible steps undone. Opened apps, websites, messages, and volume changes are not reversed."
            if all(s["status"] == "undone" for s in receipt["steps"]):
                receipt["status"] = "undone"
            self._save()
            return self._public(receipt)

    def _public(self, receipt: dict) -> dict:
        result = deepcopy(receipt)
        result["can_undo"] = bool(result.get("can_undo") and self.executor.live)
        for step in result["steps"]:
            step.pop("undo", None)
        return result

    def snapshot(self) -> dict:
        with self.lock:
            history = [self._public(r) for r in self.history]
        timers = [s["timer"] for r in history for s in r["steps"]
                  if s.get("timer") and s["status"] == "done" and s["timer"]["ends_at"] > time.time()]
        return {"history": history, "timers": timers}
