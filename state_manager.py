"""Persistent JSON deduplication state manager."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class StateManager:
    """Tracks already-notified announcement numbers so duplicates are never sent."""

    def __init__(self, state_path: str) -> None:
        self.path = Path(state_path)
        self._seen: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """Load seen announcement IDs from disk if state file exists."""
        if not self.path.exists():
            self._seen = {}
            return
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        raw_seen = data.get("seen", {})
        if isinstance(raw_seen, dict):
            self._seen = {str(k): str(v) for k, v in raw_seen.items()}
        elif isinstance(raw_seen, list):
            self._seen = {str(k): "" for k in raw_seen}
        else:
            self._seen = {}

    def is_seen(self, announcement_number: str) -> bool:
        return announcement_number in self._seen

    def mark_seen(self, announcement_number: str) -> None:
        if announcement_number not in self._seen:
            self._seen[announcement_number] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def count(self) -> int:
        return len(self._seen)

    def save(self) -> None:
        """Atomically write sorted state JSON to disk for clean git diffs."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_seen": len(self._seen),
            "seen": dict(sorted(self._seen.items())),
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".state_", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
