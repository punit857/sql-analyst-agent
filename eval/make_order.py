import json
import random
from pathlib import Path

base = Path(__file__).resolve().parent
src = sorted((base / "data").rglob("mini_dev_sqlite.json"))[0]
questions = json.loads(src.read_text(encoding="utf-8"))

SEED = 42
EXCLUDE = {518, 701}   # gold SQL timed out at 30 s, cannot be scored

pool = [q for q in questions if q["question_id"] not in EXCLUDE]
info = {q["question_id"]: (q["difficulty"], q["db_id"]) for q in pool}

rng = random.Random(SEED)
groups = {}
for q in pool:
    groups.setdefault(q["difficulty"], []).append(q["question_id"])
for ids in groups.values():
    rng.shuffle(ids)

total = len(pool)
share = {d: len(ids) / total for d, ids in groups.items()}
taken = {d: 0 for d in groups}
order = []
for k in range(total):
    # take from the difficulty that is furthest behind its share, so every prefix stays balanced
    d = max(groups, key=lambda x: share[x] * (k + 1) - taken[x] if taken[x] < len(groups[x]) else -1e9)
    order.append(groups[d][taken[d]])
    taken[d] += 1

(base / "order.json").write_text(
    json.dumps({"seed": SEED, "excluded": sorted(EXCLUDE), "order": order}), encoding="utf-8")

print("questions in order:", len(order))
for n in (20, 100, 150, 250):
    head = order[:n]
    by_diff, by_db = {}, {}
    for qid in head:
        d, db = info[qid]
        by_diff[d] = by_diff.get(d, 0) + 1
        by_db[db] = by_db.get(db, 0) + 1
    print(f"\nfirst {n}: difficulty {by_diff}")
    print("   databases:", dict(sorted(by_db.items())))