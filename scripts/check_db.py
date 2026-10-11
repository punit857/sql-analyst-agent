import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path)
tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]
for t in tables:
    n = conn.execute(f'select count(*) from "{t}"').fetchone()[0]
    print(t, n)
conn.close()
