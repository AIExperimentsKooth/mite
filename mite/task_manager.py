"""Task queue and schedule manager for Mite.

Persists task queue and scheduled tasks to ~/.mite/ as JSON files.
Supports:
  - Sequential task queue: add tasks, process them one at a time
  - Scheduled tasks: run tasks at human-readable intervals (e.g. "30m", "1h")
"""

from __future__ import annotations
import json
import os
import time
import re

_USERDATA_DIR = os.path.expanduser("~/.mite")
_QUEUE_FILE = os.path.join(_USERDATA_DIR, "queue.json")
_SCHEDULE_FILE = os.path.join(_USERDATA_DIR, "schedule.json")


# --- Interval parsing ---

_INTERVAL_PATTERNS = [
    (r'(\\d+)\\s*(h|hr|hour|hours)', 3600),
    (r'(\\d+)\\s*(m|min|minute|minutes)', 60),
    (r'(\\d+)\\s*(s|sec|second|seconds)', 1),
    (r'(\\d+)\\s*(d|day|days)', 86400),
    (r'every\\s+(\\d+)\\s*(h|hr|hour|hours)', 3600),
    (r'every\\s+(\\d+)\\s*(m|min|minute|minutes)', 60),
    (r'every\\s+(\\d+)\\s*(s|sec|second|seconds)', 1),
    (r'every\\s+(\\d+)\\s*(d|day|days)', 86400),
    (r'(?:(\\d+)\\s*h\\s*)?(\\d+)\\s*m', None),
]


def parse_interval(text: str) -> int | None:
    if not text:
        return None
    text = text.strip().lower()
    try:
        return int(text)
    except ValueError:
        pass
    composite = re.match(r'^(?:(\\d+)\\s*h\\s*)?(\\d+)\\s*m\\s*(?:(\\d+)\\s*s)?$', text)
    if composite:
        total = 0
        if composite.group(1):
            total += int(composite.group(1)) * 3600
        if composite.group(2):
            total += int(composite.group(2)) * 60
        if composite.group(3):
            total += int(composite.group(3))
        if total > 0:
            return total
    for pattern, multiplier in _INTERVAL_PATTERNS:
        m = re.match(pattern, text)
        if m:
            val = int(m.group(1))
            return val * multiplier
    return None


def format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m}m" if m else f"{h}h"
    else:
        d = seconds // 86400
        return f"{d}d"


# --- Queue ---

class TaskQueue:
    """Persistent sequential task queue."""

    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.isfile(_QUEUE_FILE):
                with open(_QUEUE_FILE) as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "tasks" in data:
                        return data
                    return {"tasks": [{"id": i + 1, "task": t, "status": "pending",
                                       "created_at": time.time()}
                                      for i, t in enumerate(data)] if data else [],
                             "processing": False}
        except Exception:
            pass
        return {"tasks": [], "processing": False}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_QUEUE_FILE), exist_ok=True)
            with open(_QUEUE_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def add(self, task: str) -> int:
        existing_ids = [t["id"] for t in self._data["tasks"]]
        new_id = max(existing_ids) + 1 if existing_ids else 1
        self._data["tasks"].append({
            "id": new_id,
            "task": task,
            "status": "pending",
            "created_at": time.time()
        })
        self._save()
        return new_id

    def list(self) -> list[dict]:
        return list(self._data["tasks"])

    def pending(self) -> list[dict]:
        return [t for t in self._data["tasks"] if t["status"] == "pending"]

    def next_pending(self) -> dict | None:
        for t in self._data["tasks"]:
            if t["status"] == "pending":
                t["status"] = "running"
                self._save()
                return t
        return None

    def mark_done(self, task_id: int):
        for t in self._data["tasks"]:
            if t["id"] == task_id:
                t["status"] = "completed"
                t["completed_at"] = time.time()
                self._save()
                return

    def mark_failed(self, task_id: int, error: str = ""):
        for t in self._data["tasks"]:
            if t["id"] == task_id:
                t["status"] = "failed"
                t["error"] = error
                self._save()
                return

    def remove(self, task_id: int) -> bool:
        before = len(self._data["tasks"])
        self._data["tasks"] = [t for t in self._data["tasks"] if t["id"] != task_id]
        if len(self._data["tasks"]) < before:
            self._save()
            return True
        return False

    def clear(self):
        self._data["tasks"] = []
        self._data["processing"] = False
        self._save()

    @property
    def processing(self) -> bool:
        return self._data.get("processing", False)

    @processing.setter
    def processing(self, val: bool):
        self._data["processing"] = val
        self._save()

    def count_pending(self) -> int:
        return sum(1 for t in self._data["tasks"] if t["status"] == "pending")


# --- Schedule ---

class TaskSchedule:
    """Persistent scheduled (recurring) task manager."""

    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.isfile(_SCHEDULE_FILE):
                with open(_SCHEDULE_FILE) as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
                    return {"entries": data or []}
        except Exception:
            pass
        return {"entries": []}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_SCHEDULE_FILE), exist_ok=True)
            with open(_SCHEDULE_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def add(self, interval_seconds: int, task: str) -> int:
        existing_ids = [e["id"] for e in self._data["entries"]]
        new_id = max(existing_ids) + 1 if existing_ids else 1
        self._data["entries"].append({
            "id": new_id,
            "interval_seconds": interval_seconds,
            "interval_label": format_interval(interval_seconds),
            "task": task,
            "next_run": time.time(),
            "last_run": None,
            "enabled": True,
        })
        self._save()
        return new_id

    def list(self) -> list[dict]:
        return list(self._data["entries"])

    def remove(self, entry_id: int) -> bool:
        before = len(self._data["entries"])
        self._data["entries"] = [e for e in self._data["entries"] if e["id"] != entry_id]
        if len(self._data["entries"]) < before:
            self._save()
            return True
        return False

    def clear(self):
        self._data["entries"] = []
        self._save()

    def check_due(self) -> list[dict]:
        now = time.time()
        return [e for e in self._data["entries"]
                if e.get("enabled", True) and e.get("next_run", 0) <= now]

    def mark_run(self, entry_id: int):
        for e in self._data["entries"]:
            if e["id"] == entry_id:
                interval = e["interval_seconds"]
                now = time.time()
                e["last_run"] = now
                e["next_run"] = now + interval
                self._save()
                return

    def disable(self, entry_id: int):
        for e in self._data["entries"]:
            if e["id"] == entry_id:
                e["enabled"] = False
                self._save()
                return

    def enable(self, entry_id: int):
        for e in self._data["entries"]:
            if e["id"] == entry_id:
                e["enabled"] = True
                e["next_run"] = time.time()
                self._save()
                return
