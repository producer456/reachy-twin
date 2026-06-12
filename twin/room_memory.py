"""Rolling room memory — a time-bounded log of what Reachy noticed while it sat
in the room: vision captions, who came and went, and what was said. Recall is
strictly ON DEMAND ("what did I miss?"); the robot never volunteers it.

This is just text (a caption is ~150 bytes), so even a 24h window is a megabyte
or two — retention length is essentially free. The GPU cost lives entirely in
the captioner (hub side), which gates on real change + Marcus being free.
"""
import json
import os
import threading
import time
from pathlib import Path


class RoomMemory:
    KINDS = ("vision", "presence", "speech")
    # Persisted next to the repo's other learned state (faces/, gestures/):
    # the timeline must survive panel/daemon restarts, or "what did I miss?"
    # comes back empty after every code deploy.
    PATH = Path(__file__).resolve().parent.parent / "room_events.json"

    def __init__(self, retention_hours=12):
        self._lock = threading.RLock()
        self._events = []            # [{t, kind, text}], oldest first
        self.retention_hours = retention_hours
        self._load()

    def _load(self):
        try:
            data = json.loads(self.PATH.read_text(encoding="utf-8"))
            cutoff = time.time() - self.retention_hours * 3600
            with self._lock:
                self._events = [e for e in data if isinstance(e, dict)
                                and float(e.get("t", 0)) >= cutoff]
        except Exception:
            pass                      # no file yet / unreadable -> start empty

    def _save(self):
        try:
            with self._lock:
                blob = json.dumps(self._events)
            tmp = str(self.PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, self.PATH)
        except Exception:
            pass                      # persistence is best-effort

    def set_retention(self, hours):
        try:
            h = int(hours)
        except (TypeError, ValueError):
            return
        with self._lock:
            self.retention_hours = max(1, min(48, h))
        self._prune()

    def add(self, kind, text):
        text = (text or "").strip()
        if not text or kind not in self.KINDS:
            return
        with self._lock:
            # collapse an immediate exact-duplicate caption (a static scene that
            # squeaks past the change gate) so the log doesn't stutter.
            if self._events and self._events[-1]["kind"] == kind \
                    and self._events[-1]["text"] == text:
                self._events[-1]["t"] = time.time()
                self._save()
                return
            self._events.append({"t": time.time(), "kind": kind, "text": text})
        self._prune()
        self._save()

    def _prune(self):
        cutoff = time.time() - self.retention_hours * 3600
        with self._lock:
            self._events = [e for e in self._events if e["t"] >= cutoff]

    # ---- read side ----

    def count(self):
        with self._lock:
            return len(self._events)

    def span_minutes(self):
        """How far back the oldest retained event is (minutes)."""
        with self._lock:
            if not self._events:
                return 0
            first = self._events[0]["t"]
        return int((time.time() - first) / 60)

    def timeline_text(self, max_events=240):
        """A compact, human-readable timeline for Marcus to narrate at recall.
        Returns (text, shown, total). Sparse by design (events only land on real
        change), so the whole window usually fits in one prompt."""
        with self._lock:
            total = len(self._events)
            evs = self._events[-max_events:]
        lines = []
        for e in evs:
            ts = time.strftime("%-I:%M %p", time.localtime(e["t"]))
            lines.append(f"{ts} - {e['text']}")
        return "\n".join(lines), len(evs), total

    def state(self):
        return {
            "retention_hours": self.retention_hours,
            "event_count": self.count(),
            "span_minutes": self.span_minutes(),
        }
