import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import DEMO_DBS, open_readonly, inspect_schema, schema_to_text

name = sys.argv[1] if len(sys.argv) > 1 else "sales"
conn = open_readonly(DEMO_DBS[name])
schema = inspect_schema(conn)
text = schema_to_text(schema)
print(text)
print()
print("tables:", len(schema), "| characters:", len(text), "| rough tokens:", len(text) // 4)

try:
    conn.execute("DELETE FROM " + list(schema)[0])
    print("WARNING: write succeeded, connection is NOT read-only")
except Exception as e:
    print("write blocked as expected:", e)
conn.close()