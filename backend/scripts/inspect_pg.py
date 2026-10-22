"""Inspect the Postgres schema + memory tables for the orchestrator DB."""
import sys

import psycopg

CONNINFO = "postgresql://orchestrator:orchestrator@127.0.0.1:5433/orchestrator"

conn = psycopg.connect(CONNINFO)
cur = conn.cursor()

cur.execute("SELECT extname FROM pg_extension ORDER BY 1")
print("extensions:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
tables = [r[0] for r in cur.fetchall()]
print("tables:", tables)

cur.execute("""SELECT indexname FROM pg_indexes
               WHERE schemaname='public' AND indexdef LIKE '%hnsw%' ORDER BY 1""")
print("hnsw indexes:", [r[0] for r in cur.fetchall()])

for table in ("episodic_runs", "semantic_memories"):
    if table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed name
        print(f"{table} rows:", cur.fetchone()[0])

cur.execute("SELECT count(*) FROM checkpoints")
print("checkpoints rows:", cur.fetchone()[0])

cur.execute("SELECT count(*) FROM messages")
print("messages rows:", cur.fetchone()[0])

cur.execute("""SELECT status, count(*) FROM runs GROUP BY status""")
print("runs by status:", cur.fetchall())

cur.execute("""SELECT substr(error, 1, 240) FROM runs
               WHERE error IS NOT NULL ORDER BY started_at DESC LIMIT 2""")
for row in cur.fetchall():
    print("last error:", row[0])

conn.close()
sys.exit(0)
