# migrate_add_landing_url.py
from sqlalchemy import text
from db import engine

sql = "ALTER TABLE campaigns ADD COLUMN landing_url VARCHAR(500) DEFAULT ''"
with engine.begin() as conn:
    try:
        conn.execute(text(sql))
        print("OK: landing_url added.")
    except Exception as e:
        print(f"NOTE: {e}")
