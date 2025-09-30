from pathlib import Path
import pandas as pd
from db import SessionLocal
from models import Base, Campaign
from db import engine

Base.metadata.create_all(bind=engine)

CSV_PATH = Path(__file__).resolve().parent / "data" / "campaigns.csv"

if not CSV_PATH.exists():
    print("No CSV found, nothing to migrate.")
    raise SystemExit

df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
migrated = 0
with SessionLocal() as db:
    for _, r in df.iterrows():
        # האם כבר קיים?
        exists = db.get(Campaign, r["id"])
        if exists:
            continue
        c = Campaign(
            id=r["id"],
            name=r.get("name",""),
            audience=r.get("audience",""),
            default_channel=r.get("channel","Email"),
            goal=r.get("goal",""),
            budget=r.get("budget","")
        )
        db.add(c)
        migrated += 1
    db.commit()

print(f"Migrated {migrated} campaigns to SQLite.")
