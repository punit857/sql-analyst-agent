import json
import sys
import time

import httpx

question = sys.argv[1] if len(sys.argv) > 1 else "How many orders were cancelled?"
source = sys.argv[2] if len(sys.argv) > 2 else "sales"
body = {"session_id": "live-api-test", "source": source, "question": question}

t0 = time.perf_counter()
with httpx.stream("POST", "http://127.0.0.1:8000/api/ask", json=body, timeout=120) as r:
    print("HTTP", r.status_code)
    if r.status_code != 200:
        r.read()
        print(r.text)
        sys.exit(1)
    event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            at = int((time.perf_counter() - t0) * 1000)
            if event == "trace":
                print(f"+{at:>5}ms trace {data['node']:<13} {data.get('decision', '')} {data.get('provider', '')}")
            elif event == "final":
                print(f"+{at:>5}ms FINAL {data['status']}: {data['message'][:200]}")
            else:
                print(f"+{at:>5}ms {event} {data}")