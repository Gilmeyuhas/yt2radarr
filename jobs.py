"""Job persistence and state management helpers for yt2radarr."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional

__all__ = [
    "JobRecord",
    "JobRepository",
    "now_iso",
]


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now_iso() -> str:
    """Return the current UTC timestamp encoded as an ISO 8601 string."""

    return datetime.utcnow().strftime(ISO_FORMAT)


@dataclass
class JobRecord:  # pylint: disable=too-many-instance-attributes
    """Dataclass representing a single job entry stored on disk."""

    id: str
    label: str = "Radarr Download"
    subtitle: str = ""
    status: str = "queued"
    progress: float = 0.0
    metadata: List[str] = field(default_factory=list)
    message: str = ""
    created_at: str = field(default_factory=now_iso)
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=now_iso)
    completed_at: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    request: Dict = field(default_factory=dict)

    def to_dict(self, include_logs: bool = False) -> Dict:
        """Serialise the record into a JSON-safe dictionary."""

        payload = {
            "id": self.id,
            "label": self.label,
            "subtitle": self.subtitle,
            "status": self.status,
            "progress": self.progress,
            "metadata": list(self.metadata),
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }
        if include_logs:
            payload["logs"] = list(self.logs)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict) -> "JobRecord":
        """Create a record from persisted JSON data."""

        kwargs = {
            "id": payload.get("id") or "",
            "label": payload.get("label") or "Radarr Download",
            "subtitle": payload.get("subtitle") or "",
            "status": payload.get("status") or "queued",
            "progress": float(payload.get("progress") or 0),
            "metadata": list(payload.get("metadata") or []),
            "message": payload.get("message") or "",
            "created_at": payload.get("created_at") or now_iso(),
            "started_at": payload.get("started_at"),
            "updated_at": payload.get("updated_at") or now_iso(),
            "completed_at": payload.get("completed_at"),
            "logs": list(payload.get("logs") or []),
            "request": dict(payload.get("request") or {}),
        }
        return cls(**kwargs)


class JobRepository:  # pylint: disable=too-many-instance-attributes
    """Thread-safe JSON-backed job repository."""

    def __init__(self, path: str, *, max_items: int = 50, max_logs: int = 200) -> None:
        self._path = path
        self._max_items = max_items
        self._max_logs = max_logs
        self._cache: List[JobRecord] = []
        self._loaded = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raw = []
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - disk issues
            print(f"Failed to load job history: {exc}")
            raw = []
        self._cache = [JobRecord.from_dict(entry) for entry in raw if isinstance(entry, dict)]
        self._loaded = True

    def _persist_locked(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as handle:
            json.dump([record.__dict__ for record in self._cache], handle, indent=2)

    def _insert_locked(self, record: JobRecord) -> JobRecord:
        self._cache.insert(0, record)
        if len(self._cache) > self._max_items:
            self._cache = self._cache[: self._max_items]
        self._persist_locked()
        return record

    def _touch_locked(self, record: JobRecord) -> JobRecord:
        record.updated_at = now_iso()
        self._persist_locked()
        return record

    def _find_locked(self, job_id: str) -> Optional[JobRecord]:
        for record in self._cache:
            if record.id == job_id:
                return record
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create(self, job_data: Dict) -> Dict:
        """Add a job to the history and return its serialised form."""

        record = JobRecord.from_dict(job_data)
        if not record.id:
            raise ValueError("Job identifier is required")
        with self._lock:
            self._ensure_loaded()
            record.created_at = record.created_at or now_iso()
            record.updated_at = record.updated_at or record.created_at
            self._insert_locked(record)
            return record.to_dict(include_logs=True)

    def list(self, *, include_logs: bool = False) -> List[Dict]:
        """Return known jobs sorted by creation time."""

        with self._lock:
            self._ensure_loaded()
            items = [record.to_dict(include_logs=include_logs) for record in self._cache]
        items.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
        return items

    def get(self, job_id: str, *, include_logs: bool = False) -> Optional[Dict]:
        """Return a single job as a dictionary if it exists."""

        with self._lock:
            self._ensure_loaded()
            record = self._find_locked(job_id)
            if record is None:
                return None
            return record.to_dict(include_logs=include_logs)

    def update(self, job_id: str, updates: Dict) -> Optional[Dict]:
        """Apply updates to a job and return the refreshed payload."""

        with self._lock:
            self._ensure_loaded()
            record = self._find_locked(job_id)
            if record is None:
                return None
            progress = updates.get("progress")
            if progress is not None:
                try:
                    progress_value = float(progress)
                except (TypeError, ValueError):
                    progress_value = record.progress
                record.progress = max(
                    record.progress, 0.0, min(100.0, progress_value)
                )
            if "status" in updates:
                record.status = str(updates["status"]) or record.status
            if "label" in updates and updates["label"]:
                record.label = str(updates["label"])
            if "subtitle" in updates and updates["subtitle"] is not None:
                record.subtitle = str(updates["subtitle"])
            if "metadata" in updates and updates["metadata"] is not None:
                record.metadata = list(updates["metadata"])
            if "message" in updates and updates["message"] is not None:
                record.message = str(updates["message"])
            if "request" in updates and updates["request"] is not None:
                record.request = dict(updates["request"])
            if "started_at" in updates and updates["started_at"]:
                if not record.started_at:
                    record.started_at = str(updates["started_at"])
            if "completed_at" in updates and updates["completed_at"]:
                record.completed_at = str(updates["completed_at"])
            self._touch_locked(record)
            return record.to_dict(include_logs=True)

    def append_logs(self, job_id: str, messages: Iterable[str]) -> None:
        """Add log entries to a job, trimming history when needed."""

        payload = [str(message) for message in messages]
        if not payload:
            return
        with self._lock:
            self._ensure_loaded()
            record = self._find_locked(job_id)
            if record is None:
                return
            record.logs.extend(payload)
            if len(record.logs) > self._max_logs:
                record.logs = record.logs[-self._max_logs :]
            self._touch_locked(record)

    def replace_last_log(self, job_id: str, message: str) -> None:
        """Overwrite the most recent log entry for the job."""

        text = str(message)
        with self._lock:
            self._ensure_loaded()
            record = self._find_locked(job_id)
            if record is None:
                return
            if record.logs:
                record.logs[-1] = text
            else:
                record.logs.append(text)
            self._touch_locked(record)

    def mark_failure(self, job_id: str, message: str) -> Optional[Dict]:
        """Flag a job as failed and record its completion."""

        return self.update(
            job_id,
            {
                "status": "failed",
                "message": message,
                "progress": 100,
                "completed_at": now_iso(),
            },
        )

    def mark_success(self, job_id: str) -> Optional[Dict]:
        """Mark a job as successfully completed."""

        return self.update(
            job_id,
            {
                "status": "complete",
                "message": "",
                "progress": 100,
                "completed_at": now_iso(),
            },
        )

    def status(
        self, job_id: str, status: str, *, progress: Optional[float] = None
    ) -> Optional[Dict]:
        """Convenience helper to update status/progress fields."""

        updates: Dict = {"status": status}
        if status == "processing":
            updates.setdefault("started_at", now_iso())
        if progress is not None:
            updates["progress"] = progress
        return self.update(job_id, updates)
