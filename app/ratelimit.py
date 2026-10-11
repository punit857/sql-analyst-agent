import math
import threading
import time
from collections import deque


class Rule:
    """At most `limit` hits per `window_s` seconds, counted per IP or across everyone."""

    def __init__(self, name, scope, limit, window_s, message):
        assert scope in ("ip", "global")
        self.name = name
        self.scope = scope
        self.limit = limit
        self.window_s = window_s
        self.message = message


class Decision:
    def __init__(self, allowed, retry_after=0, message=""):
        self.allowed = allowed
        self.retry_after = retry_after
        self.message = message


class RateLimiter:
    """In-memory sliding-window limiter. Fine for one instance; state is lost on restart."""

    def __init__(self, rules, clock=time.monotonic, max_keys=5000):
        self.rules = list(rules)
        self.enabled = True
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits = {r.name: {} for r in self.rules}

    def _key(self, rule, ip):
        return ip if rule.scope == "ip" else "*"

    def _live(self, rule, key, now):
        table = self._hits[rule.name]
        q = table.get(key)
        if q is None:
            return None
        cutoff = now - rule.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del table[key]
            return None
        return q

    def check(self, ip):
        """Allow and record the hit, or refuse without recording anything."""
        if not self.enabled:
            return Decision(True)
        with self._lock:
            now = self._clock()
            worst = None
            for rule in self.rules:
                q = self._live(rule, self._key(rule, ip), now)
                if q is not None and len(q) >= rule.limit:
                    wait = q[0] + rule.window_s - now
                    if worst is None or wait > worst[0]:
                        worst = (wait, rule)
            if worst is not None:
                return Decision(False, max(1, math.ceil(worst[0])), worst[1].message)
            for rule in self.rules:
                table = self._hits[rule.name]
                table.setdefault(self._key(rule, ip), deque()).append(now)
                while len(table) > self.max_keys:
                    table.pop(next(iter(table)))  # forget the oldest-seen address
            return Decision(True)