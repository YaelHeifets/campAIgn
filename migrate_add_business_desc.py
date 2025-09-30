# migrate_add_business_desc.py
from sqlalchemy import text
from db import engine

sql = "ALTER TABLE campaigns ADD COLUMN business_desc VARCHAR(500) DEFAULT ''"
with engine.begin() as conn:
    try:
        conn.execute(text(sql))
        print("OK: business_desc added.")
    except Exception as e:
        
        print(f"NOTE: {e}")
