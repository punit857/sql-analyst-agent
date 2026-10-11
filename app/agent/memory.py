import threading
import time
from collections import OrderedDict

MAX_TURNS = 6            # turns kept per session
MAX_SESSIONS = 200       # sessions kept in memory in total
TTL_S = 30 * 60          # a session expires 30 minutes after its last turn
MAX_SID_LEN = 64
MAX_Q_CHARS = 500
MAX_ANSWER_CHARS = 300


class SessionStore:
    """In-memory conversation history. Nothing is written to disk."""

    def __init__(self, max_sessions=MAX_SESSIONS, ttl_s=TTL_S,
                 max_turns=MAX_TURNS, clock=time.monotonic):
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s
        self.max_turns = max_turns
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions = OrderedDict()  # sid -> {"source", "touched", "turns"}

    def _purge(self, now):
        expired = [s for s, v in self._sessions.items() if now - v["touched"] > self.ttl_s]
        for s in expired:
            del self._sessions[s]
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)  # drop the least recently used

    def get_history(self, sid, source):
        """Turns for this session and data source, oldest first. Empty if none."""
        sid = str(sid)[:MAX_SID_LEN]
        with self._lock:
            self._purge(self._clock())
            s = self._sessions.get(sid)
            if s is None or s["source"] != source:
                return []
            return [dict(t) for t in s["turns"]]

    def add_turn(self, sid, source, turn):
        """Store one finished turn. Switching source starts a fresh history."""
        sid = str(sid)[:MAX_SID_LEN]
        clean = {
            "question": str(turn.get("question", ""))[:MAX_Q_CHARS],
            "sql": str(turn.get("sql", "")),
            "columns": [str(c) for c in turn.get("columns", [])][:20],
            "row_count": int(turn.get("row_count", 0)),
            "answer": str(turn.get("answer", ""))[:MAX_ANSWER_CHARS],
            "kind": str(turn.get("kind", "answered"))[:12],
        }
        with self._lock:
            now = self._clock()
            s = self._sessions.get(sid)
            if s is None or s["source"] != source:
                s = {"source": source, "touched": now, "turns": []}
                self._sessions[sid] = s
            s["turns"].append(clean)
            del s["turns"][:-self.max_turns]
            s["touched"] = now
            self._sessions.move_to_end(sid)
            self._purge(now)

    def clear(self, sid):
        with self._lock:
            self._sessions.pop(str(sid)[:MAX_SID_LEN], None)

    def count(self):
        with self._lock:
            self._purge(self._clock())
            return len(self._sessions)