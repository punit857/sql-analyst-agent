import threading
import time
import uuid
from collections import OrderedDict

MAX_ITEMS = 20
MAX_TOTAL_BYTES = 100 * 1024 * 1024
TTL_S = 30 * 60


class UploadStore:
    """Uploaded databases, held as bytes in memory only. One upload per session."""

    def __init__(self, max_items=MAX_ITEMS, max_total_bytes=MAX_TOTAL_BYTES,
                 ttl_s=TTL_S, clock=time.monotonic):
        self.max_items = max_items
        self.max_total_bytes = max_total_bytes
        self.ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._items = OrderedDict()

    def _total(self):
        return sum(len(v["data"]) for v in self._items.values())

    def _purge(self, now):
        for k in [k for k, v in self._items.items() if now - v["touched"] > self.ttl_s]:
            del self._items[k]
        while len(self._items) > 1 and (
            len(self._items) > self.max_items or self._total() > self.max_total_bytes
        ):
            self._items.popitem(last=False)  # drop the least recently used

    def put(self, sid, data, profile, static):
        with self._lock:
            now = self._clock()
            self._items.pop(sid, None)
            entry = {"data": data, "profile": profile, "static": static, "notes": "",
                     "version": uuid.uuid4().hex[:8], "touched": now}
            self._items[sid] = entry
            self._purge(now)
            return entry

    def set_notes(self, sid, notes):
        """Attach the user's own definitions to their upload. False if there is no live upload."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            entry = self._items.get(sid)
            if entry is None:
                return False
            entry["notes"] = notes
            entry["touched"] = now
            self._items.move_to_end(sid)
            return True

    def get(self, sid):
        with self._lock:
            now = self._clock()
            self._purge(now)
            entry = self._items.get(sid)
            if entry is not None:
                entry["touched"] = now
                self._items.move_to_end(sid)
            return entry

    def clear(self, sid):
        with self._lock:
            self._items.pop(sid, None)

    def count(self):
        with self._lock:
            self._purge(self._clock())
            return len(self._items)