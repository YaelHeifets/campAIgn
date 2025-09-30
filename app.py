# --- Imports ---
import re
import os, io, zipfile
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort, session
from dotenv import load_dotenv
from sqlalchemy import select, or_, func

from db import engine, SessionLocal
from models import Base, Campaign, Asset
from auth import auth_bp, login_required
try:
    from publishers import LocalFilePublisher, SendGridEmailPublisher
except Exception:
    # אם publishers.py לא מכיל SendGridEmailPublisher או שיש בעיית ייבוא
    from publishers import LocalFilePublisher
    SendGridEmailPublisher = None

# --- Setup & paths ---
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")

def get_publish_mode() -> str:
    """הצגת מצב הפרסום בכותרת: SendGrid / Local."""
    mode = (os.getenv("PUBLISH_MODE") or "local").lower()
    return "SendGrid" if (
        mode == "sendgrid"
        and os.getenv("SENDGRID_API_KEY")
        and os.getenv("SENDGRID_FROM")
    ) else "Local"

# הזרקה גלובלית לתבניות
@app.context_processor
def inject_user():
    return {
        "current_user": session.get("user"),
        "publish_mode": get_publish_mode(),
    }

# Blueprints
app.register_blueprint(auth_bp)

# יצירת טבלאות אם לא קיימות
Base.metadata.create_all(bind=engine)

# הבטחה לעמודה archived_at אם לא קיימת (SQLite)
def _ensure_archived_column():
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE campaigns ADD COLUMN archived_at TEXT")
    except Exception:
        # כבר קיימת או DB אחר – מתעלמים
        pass

_ensure_archived_column()

# ספריות/קבצים לתוצרים (פעם אחת, בלי כפילויות)
DATA_DIR    = BASE_DIR / "data"
BRIEFS_DIR  = DATA_DIR / "briefs"
CONTENT_DIR = DATA_DIR / "content"
RECIP_DIR   = DATA_DIR / "recipients"

for p in (DATA_DIR, BRIEFS_DIR, CONTENT_DIR, RECIP_DIR):
    p.mkdir(parents=True, exist_ok=True)

VALID_CHANNELS = {"Email", "SMS", "Social", "Ads"}

# ---------- Helpers ----------
def now_utc():
    return datetime.now(timezone.utc)

def get_campaign(campaign_id: str):
    """מחזיר dict עם נתוני קמפיין מה-DB, או None אם לא קיים."""
    with SessionLocal() as db:
        row = db.execute(select(Campaign).where(Campaign.id == str(campaign_id))).first()
        if not row:
            return None
        (c,) = row
        return {
            "id": c.id,
            "name": c.name or "",
            "audience": c.audience or "",
            "channel": c.default_channel or "Email",
            "goal": c.goal or "",
            "budget": c.budget or "",
            "business_desc": getattr(c, "business_desc", "") or "",
            "landing_url": getattr(c, "landing_url", "") or "",
            "created_at": (c.created_at or datetime.now_utc()).strftime("%Y-%m-%d %H:%M:%S"),
        }


def brief_path(campaign_id: str) -> Path:
    return BRIEFS_DIR / f"{campaign_id}.txt"

def content_path(campaign_id: str, channel: str) -> Path:
    channel = (channel or "").capitalize()
    return CONTENT_DIR / f"{campaign_id}_{channel.lower()}.txt"

def results_summary(campaign_id: str):
    """סטטוס מהיר למסך תוצאות: קיום תוצרים, תקציר, וכמות פרסומים."""
    channels = ["Email", "SMS", "Social", "Ads"]

    # האם יש תקציר
    bp = brief_path(campaign_id)
    brief_exists = bp.exists()

    # כמה תוצרים מוכנים
    assets_exist = 0
    total_assets = len(channels)
    for ch in channels:
        p = content_path(campaign_id, ch)
        if p.exists():
            assets_exist += 1

    # כמה "פרסומים" נשמרו (LocalFilePublisher) ב-data/published
    pub_dir = DATA_DIR / "published"
    pub_dir.mkdir(parents=True, exist_ok=True)
    sent_count = len(list(pub_dir.glob(f"{campaign_id}_*.json")))

    return {
        "brief_exists": brief_exists,
        "assets_exist": assets_exist,
        "total_assets": total_assets,
        "sent_count": sent_count,
    }

def recipients_path(campaign_id: str) -> Path:
    return RECIP_DIR / f"{campaign_id}.csv"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def parse_emails(text: str) -> list[str]:
    """
    מקבל טקסט (מכל סוג: שורות, מופרד בפסיקים/נקודה־פסיק/רווחים)
    ומחזיר רשימת אימיילים ייחודית, בסדר הופעה.
    """
    if not text:
        return []
    # מאחד מפרידים שונים לשורה
    for sep in [",", ";", "\t"]:
        text = text.replace(sep, "\n")
    text = "\n".join(part.strip() for part in text.splitlines())
    seen, out = set(), []
    for line in text.splitlines():
        line = line.strip().strip("<>").strip('"').strip("'")
        if not line:
            continue
        if EMAIL_RE.match(line) and line not in seen:
            seen.add(line); out.append(line)
    return out

def load_recipients(campaign_id: str) -> list[str]:
    """
    טוען רשימת נמענים מקובץ CSV של הקמפיין.
    מחזיר רשימת אימיילים או רשימה ריקה אם הקובץ לא קיים.
    """
    path = recipients_path(campaign_id)
    if not path.exists():
        return []
    
    try:
        text = path.read_text(encoding="utf-8")
        return parse_emails(text)
    except Exception:
        return []

def has_openai() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

def generate_brief_text(camp: dict) -> str:
    """יוצר טקסט תקציר (OpenAI אם יש מפתח, אחרת fallback)."""
    name = camp.get("name", "")
    audience = camp.get("audience", "")
    channel = camp.get("channel", "")
    goal = camp.get("goal", "")
    budget = camp.get("budget", "")

    fallback = f"""CampAIgn – Campaign Brief
===============================
Campaign Name: {name}
Primary Goal: {goal or 'N/A'}
Target Audience: {audience}
Primary Channel: {channel}
Budget (₪): {budget or 'N/A'}

Key Message:
- הצעת ערך קצרה וברורה שמדברת בשפה של {audience}.
- קריאה לפעולה ממוקדת להשגת "{goal or 'היעד'}".

Channel Strategy:
- {channel}: מסר מותאם לערוץ, כותרת מושכת ו-CTA בולט.
- לוח זמנים: השקה → תזכורת → דחיפה אחרונה.

First Draft Copy (Hebrew):
- כותרת: {name} – בדיוק מה שחיפשתן.
- גוף: מצטרפות אלינו? {goal or 'להצטרפות/רישום/רכישה'} בלחיצה אחת. קצר, חד וברור.
- CTA: לחצו כאן עכשיו.

Metrics:
- מדד הצלחה: המרות ל-{goal or 'היעד'}.
- מדדים משלימים: CTR, פתיחות, הקלקות, עלות/המרה.
"""
    if not has_openai():
        return fallback

    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = (
            "כתבי תקציר קמפיין קצר ומקצועי בעברית, נקי ומעשי, עם כותרת, מסר מרכזי, קריאה לפעולה, "
            "וטיוטת קופי ראשונה לערוץ הנבחר. קיצור ודיוק.\n"
            f"שם קמפיין: {name}\n"
            f"קהל יעד: {audience}\n"
            f"ערוץ: {channel}\n"
            f"מטרה: {goal or 'N/A'}\n"
            f"תקציב (₪): {budget or 'N/A'}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        text = resp.choices[0].message.content.strip()
        return f"CampAIgn – Campaign Brief (AI)\n===============================\n{text}\n"
    except Exception:
        return fallback

def generate_channel_content(camp: dict, channel: str, tone: str = "professional") -> str:
    """
    מייצרת תוכן לפי ערוץ (Email / SMS / Social / Ads) וטון כתיבה.
    משתמשת ב-OpenAI אם יש מפתח; אחרת נופלת לפולבק איכותי בלי תגים מוזרים.
    """

    # --- נרמול ערוץ וטון ---
    ch = (channel or "").strip().lower()
    if ch in ("email", "e-mail", "mail"):
        channel_cap = "Email"
    elif ch == "sms":
        channel_cap = "SMS"
    elif ch in ("social", "facebook", "instagram", "tiktok", "x", "twitter", "רשת", "רשתות"):
        channel_cap = "Social"
    elif ch in ("ads", "ad", "מודעות", "פרסום"):
        channel_cap = "Ads"
    else:
        channel_cap = "Social"  # ברירת מחדל סבירה

    tone = (tone or "professional").strip().lower()

    # --- נתוני קמפיין ---
    name     = (camp.get("name") or "").strip()
    audience = (camp.get("audience") or "").strip()
    goal     = (camp.get("goal") or "").strip() or "הצטרפות / הרשמה / רכישה"
    biz      = (camp.get("business_desc") or "").strip()
    url      = (camp.get("landing_url") or "").strip()

    # --- מיפוי טונים לתיאור בעברית ---
    tone_map = {
        "professional": "מקצועי",
        "friendly":     "ידידותי",
        "sharp":        "חד וישיר",
        "humorous":     "הומוריסטי עדין",
        "formal":       "רשמי",
    }
    tone_he = tone_map.get(tone, "מקצועי")

    # --- עוזרים קטנים ---
    def clean(s: str) -> str:
        return (s or "").replace("\r\n", "\n").strip()

    def short_benefit(biz_txt: str, goal_txt: str) -> str:
        b = (biz_txt or "").lower()
        g = (goal_txt or "").lower()
        # דוגמאות להיגיון עדין
        if any(k in b for k in ("פימו", "יצירה", "קייטנה", "חוג", "סדנה")):
            if "סוכות" in name or "חג" in g:
                return "מקומות אחרונים לחגים"
            return "סדנה חווייתית – הרשמה מהירה"
        if "וובינר" in g or "webinar" in g:
            return "וובינר חינמי – הירשמו עכשיו"
        if any(k in g for k in ("רישום", "הרשמה", "הצטרפות")):
            return "הצטרפות בקליק אחד"
        if any(k in g for k in ("מכירה", "רכישה", "קנייה", "קניה")):
            return "הטבה לזמן מוגבל"
        return goal_txt or "הצעה שלא כדאי לפספס"

    def polite_cta(link: str) -> str:
        return f"לפרטים והרשמה: {link}" if link else "ענו למייל הזה ונחזור אליכן במהירות."

    # טיוב קצר למסר לפי טון עבור SMS (קצר ושונה לכל טון)
    import re
    def style_by_tone_sms(text: str, tone_key: str) -> str:
        base = (text or "").strip()
        t = (tone_key or "professional").lower()
        if t == "friendly":
            return (base + " 🙂").strip()
        if t == "sharp":
            one = re.sub(r"\s+", " ", base)
            if not one.endswith("."):
                one += "."
            return (one + " נרשמות עכשיו.").strip()
        if t == "humorous":
            return (base + " 😉").strip()
        if t == "formal":
            return base.replace("!", "׃").strip()
        return base

    # --- POLLBACK (כשאין OpenAI או במקרה כשל) ---
    benefit = short_benefit(biz, goal)

    if channel_cap == "Email":
        subject   = f"{name} — {benefit}"
        preheader = "מקומות אחרונים • הרשמה מהירה" if "מקומות" in benefit else "הטבה לזמן מוגבל • הצטרפות מהירה"
        body_lines = [
            "היי,",
            f"{biz or name} מזמין אותך להצטרף – {goal}.",
            "למי זה מתאים?",
            f"• {audience}" if audience else "• לקהל היעד הרלוונטי",
            "למה עכשיו?",
            f"• {benefit}",
            "",
            polite_cta(url),
            "",
            f"תודה, צוות {name or 'CampAIgn'}"
        ]
        fallback = clean(f"Subject: {subject}\nPreheader: {preheader}\n\n" + "\n".join(body_lines))

    elif channel_cap == "SMS":
        core = f"{name}: {benefit}"
        if url:
            core += f" {url}"
        sms_text = style_by_tone_sms(core, tone)
        fallback = clean(sms_text[:150])

    elif channel_cap == "Social":
        hashtags = []
        if biz:
            hb = re.sub(r"[^א-תA-Za-z0-9 ]+", "", biz).replace(" ", "")
            if hb:
                hashtags.append(f"#{hb}")
        if name:
            hn = re.sub(r"[^א-תA-Za-z0-9 ]+", "", name).replace(" ", "")
            if hn:
                hashtags.append(f"#{hn}")
        if not hashtags:
            hashtags = ["#SmallBiz", "#CampAIgn"]

        caption = f"{name} — {benefit}\n{polite_cta(url)}\n" + " ".join(hashtags[:3])
        fallback = clean(caption)

    else:  # Ads
        headline = f"{name} — {benefit}"
        body     = f"{biz or name}: {goal}. קצר, פשוט וממוקד."
        cta      = "להצטרפות" if any(k in goal for k in ("הצטרפות", "רישום", "הרשמה")) else "לפרטים"
        ad = f"Headline: {headline}\nBody: {body}\nCTA: {cta}" + (f"\nURL: {url}" if url else "")
        fallback = clean(ad)

    # --- אם אין מפתח OpenAI – מחזירות פולבק איכותי ---
    if not has_openai():
        return fallback

    # --- נסיון AI מבוקר (ללא סוגריים מרובעים, ללא תגים) ---
    try:
        from openai import OpenAI
        client = OpenAI()

        # הנחיות פורמט קשיחות כדי שלא יופיעו תגים [כאלה]
        # והדגשה שלא להשתמש ב"אבל שלום לקוחות קיימים/חדשים" כסלמונלה — לא לפנות בשמות קהל יעד כפולים.
        prompt = (
            "כתבי תוכן בעברית לקמפיין לפי ערוץ וטון. אסור להשתמש בסוגריים מרובעים או תגים מלאכותיים כלשהם.\n"
            "התייחסי ל'קהל יעד' כהגדרת פרסונה/התאמה, אבל אל תכתבי 'שלום לקוחות קיימים, לקוחות חדשים'.\n"
            "התאימי את המבנה לערוץ:\n"
            "• Email: שדות 'Subject:' ואז 'Preheader:' ואז 'Body:' בשורות נפרדות; גוף קצר, ענייני, עם CTA. אם יש URL – שילבי אותו טבעי.\n"
            "• SMS: עד 150 תווים, משפט אחד ברור + URL אם קיים; הקפידי על הטון המבוקש.\n"
            "• Social: פוסט קצר 2–4 שורות + 1–3 האשטאגים רלוונטיים; CTA קצר; URL אם קיים.\n"
            "• Ads: שלושה שדות 'Headline:', 'Body:', 'CTA:' ובמידה ויש URL – 'URL:' בסוף.\n"
            "שימרי על עברית טבעית, ללא פלצנות, וללא תוספות סגנוניות מלאכותיות.\n\n"
            f"שם קמפיין: {name}\n"
            f"תחום/עסק: {biz or 'לא צוין'}\n"
            f"קהל יעד: {audience or 'לא צוין'}\n"
            f"מטרה: {goal}\n"
            f"ערוץ: {channel_cap}\n"
            f"טון כתיבה: {tone_he}\n"
            f"כתובת יעד: {url or '—'}\n"
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        ai_text = clean(resp.choices[0].message.content)

        # עידון נוסף ל-SMS (במקרה שהמודל התפרע)
        if channel_cap == "SMS":
            ai_text = style_by_tone_sms(ai_text, tone)[:150]

        # הגנה כפולה: אם יצא ריק/גרוע – פולבק
        return ai_text or fallback

    except Exception:
        return fallback

    def bullets_from_segs():
        if not segs:
            return "• חיסכון בזמן וכסף\n• פתרון אמיתי לצורך שלכם\n• התחלה מיידית, בלחיצה אחת"
        return "\n".join([f"• מתאים במיוחד ל{seg}" for seg in segs[:3]])

    # ---- Fallback (ללא OpenAI) ----
    if not has_openai():
        if channel_cap == "Email":
            subject = f"[פרסומת] {name} – {goal}"
            body = f"""{sal}
מה מקבלים?
{bullets_from_segs()}

למה עכשיו?
• הטבה לזמן מוגבל
• מספר מקומות/כמות מוגבלת

איך מתחילים?
• נכנסות לקישור ונרשמות: {url}

תיאור קצר:
{biz or name}
"""
            return f"Subject: {subject}\nBody:\n{body}".strip()

        if channel_cap == "Sms":
            core = f"{name}: {goal}. לפרטים: {url}"
            core = limit_chars(core, 160)
            return f"(טון: {tone_he})\n{core}"


        if channel_cap == "Social":
            post = f"""{sal} {name} יוצא לדרך!
            {goal} בלחיצה: {url}

{('#' + name.replace(' ', '')) if name else ''} #CampAIgn"""
            return f"(טון: {tone_he})\n{post}".strip()

        # Ads
        ads = f"""כותרת: {name} – זה הזמן
גוף: {goal} במהירות ובקלות. {('מותאם ל' + ' / '.join(segs)) if segs else ''}.
CTA: התחילו עכשיו • {url}
"""
        return f"(טון: {tone_he})\n{ads}".strip()

    # ---- OpenAI ----
    try:
        from openai import OpenAI
        client = OpenAI()

        system = (
            "את כותבת קופי שיווקי בעברית. כתבי מסר קצר, ברור וחד. "
            "לעולם אל תכללי את שמות פלחי הקהל (כמו 'לקוחות קיימים/חדשים') בברכת הפתיחה. "
            "השתמשי בברכה ניטרלית (שלום/היי) לפי הערוץ. "
            "שלבי תועלות המתאימות לפלחי הקהל בתוכן עצמו. "
            "אם הערוץ הוא Email, החזירי במדויק בפורמט: 'Subject: ...\\nBody:\\n...'. "
            "ל-SMS – עד ~160 תווים; ל-Social – פוסט קצר; ל-Ads – כותרת, גוף ו-CTA."
        )

        user = (
            f"שם קמפיין: {name}\n"
            f"סוג העסק: {biz or 'לא סופק'}\n"
            f"פלחי קהל (לא בברכה): {', '.join(segs) if segs else 'לא סופק'}\n"
            f"יעד (Goal): {goal}\n"
            f"ערוץ: {channel_cap}\n"
            f"טון כתיבה: {tone_he}\n"
            f"כתובת נחיתה: {url}\n"
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text
    except Exception:
        # נפילה ב-AI -> fallback
        if channel_cap == "Email":
            subject = f"[פרסומת] {name} – {goal}"
            body = f"""{sal}
מה מקבלים?
{bullets_from_segs()}

למה עכשיו?
• הטבה לזמן מוגבל
• מספר מקומות/כמות מוגבלת

איך מתחילים?
• נכנסות לקישור ונרשמות: {url}

תיאור קצר:
{biz or name}
"""
            return f"Subject: {subject}\nBody:\n{body}".strip()
        if channel_cap == "Sms":
            return f"(טון: {tone_he})\n{name}: {goal}. לפרטים: {url}"
        if channel_cap == "Social":
            return f"(טון: {tone_he})\nהיי! {name} יוצא לדרך!\n{goal} בלחיצה: {url}"
        return f"(טון: {tone_he})\nכותרת: {name} – זה הזמן\nגוף: {goal}. CTA: התחילו עכשיו • {url}"

def touch_campaign(campaign_id: str):
    """מעדכן updated_at=UTC עכשיו עבור הקמפיין."""
    with SessionLocal() as db:
        row = db.execute(select(Campaign).where(Campaign.id == str(campaign_id))).first()
        if row:
            (c,) = row
            c.updated_at = now_utc()
            db.commit()

def get_publisher():
    """בחר Publisher לפי משתני סביבה: PUBLISH_MODE=sendgrid/local, עם fallback בטוח."""
    mode = (os.getenv("PUBLISH_MODE") or "local").lower()

    if (
        mode == "sendgrid"
        and SendGridEmailPublisher is not None
        and os.getenv("SENDGRID_API_KEY")
        and os.getenv("SENDGRID_FROM")
    ):
        return SendGridEmailPublisher(
            api_key=os.getenv("SENDGRID_API_KEY"),
            from_email=os.getenv("SENDGRID_FROM"),
            to_email_default=os.getenv("SENDGRID_TO")  # אופציונלי
        )

    # ברירת מחדל: פרסום במדיה
    published_dir = DATA_DIR / "published"
    return LocalFilePublisher(published_dir)

def generate_channel_ideas(camp: dict, channel: str, tone: str = "professional", n: int = 3) -> list[str]:
    """
    מחזיר n רעיונות (Hooks/Angles) איכותיים, קצרים ומעשיים.
    כל רעיון: עד 2–3 שורות, בלי פלייסהולדרים/תגים, בלי דקלום 'קהל יעד'.
    """
    import os

    name   = (camp.get("name") or "").strip()
    biz    = (camp.get("business_desc") or "").strip()
    goal   = (camp.get("goal") or "").strip()
    url    = (camp.get("landing_url") or os.getenv("BUSINESS_URL") or "").strip()
    channel_cap = (channel or "").capitalize()

    tone_map = {
        "professional": "מקצועי",
        "friendly":     "ידידותי",
        "sharp":        "חד וישיר",
        "humorous":     "הומוריסטי עדין",
        "formal":       "רשמי",
    }
    tone_he = tone_map.get(tone, "מקצועי")

    base = [
        f"{name}: זווית תועלת אחת חדה + הוכחה קצרה (ניסיון/תוצאה) → CTA.",
        f"סיפורון לקוח/ה (שורה אחת) + מה יצא לו/ה מזה → CTA.",
        f"הצעה לזמן מוגבל (ללא תאריכים קשיחים אם לא נמסרו) + פעולה פשוטה → CTA.",
        f"פירוק התנגדות נפוצה במשפט → הבטחת פתרון קצרה → CTA.",
    ][:n]

    if not has_openai():
        return base

    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = (
            "תני " + str(n) + " רעיונות קצרים (Hooks/Angles) לכתיבה בערוץ, 1–3 שורות כל אחד.\n"
            "בלי סוגריים מרובעים/פלייסהולדרים; בלי להזכיר 'קהל היעד' כלשונו.\n"
            "כל רעיון עצמאי ומעשי, אפשר עם CTA עדין. אם יש URL – מותר בשורה נפרדת.\n\n"
            f"שם קמפיין: {name or '—'}\n"
            f"סוג עסק: {biz or '—'}\n"
            f"מטרה: {goal or '—'}\n"
            f"ערוץ: {channel_cap}\n"
            f"טון כתיבה: {tone_he}\n"
            f"URL: {url or '—'}\n"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.8,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        cleaned = raw.replace("[", "").replace("]", "").strip()
        # מפרקות לרעיונות לפי שורות ריקות או מקפים
        parts = [p.strip("•- \n\t") for p in cleaned.split("\n\n") if p.strip()]
        if len(parts) < n:
            # fallback פיצול עדין
            parts = [p.strip("•- \n\t") for p in cleaned.split("\n") if p.strip()]
        ideas = [i for i in parts if i][:n]
        return ideas or base
    except Exception:
        return base

# === Helpers: audience & copy ===
def parse_audience_segments(audience_raw: str) -> list[str]:
    """מפצל קהל יעד לקטגוריות שימושיות (לא לשימוש בברכה!)."""
    if not audience_raw:
        return []
    import re
    parts = re.split(r"[,\|/;·•]+|\s+-\s+|\s+–\s+", audience_raw)
    segs = [p.strip() for p in parts if p and p.strip()]
    seen, clean = set(), []
    for s in segs:
        key = s.lower()
        if key in seen or len(s) < 2:
            continue
        seen.add(key)
        clean.append(s)
    return clean[:4]

def neutral_salutation(channel: str) -> str:
    """ברכה ניטרלית לפי ערוץ (לא משתמשים ב-audience)."""
    ch = (channel or "").lower()
    if ch == "sms":
        return ""
    if ch in ("social", "ads"):
        return "היי!"
    return "שלום!"

def get_landing_url(camp: dict) -> str:
    """עדיפות ל-landing_url אם הוגדר בקמפיין, אחרת קישור כללי."""
    url = (camp.get("landing_url") or "").strip()
    return url if url else "https://example.com"

def tone_hebrew(tone: str) -> str:
    return {
        "professional": "מקצועי",
        "friendly": "ידידותי",
        "sharp": "חד וישיר",
        "humorous": "עם נימה הומוריסטית עדינה",
        "formal": "רשמי",
    }.get((tone or "").lower(), "מקצועי")

def limit_chars(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    # חותכות במילה קרובה ומוסיפות …
    cut = t[:max_chars-1]
    last_space = cut.rfind(" ")
    if last_space > 40:
        cut = cut[:last_space]
    return cut + "…"

# ---------- Routes ----------

@app.route("/")
def home():
    """דף הבית עם חיפוש/פילטר + הסתרת קמפיינים בארכיון."""
    q  = (request.args.get("q") or "").strip()
    ch = (request.args.get("channel") or "").strip()

    stmt = select(Campaign)

    # 2) מסתירים ארכיוניים (מחיקה רכה)
    stmt = stmt.where(Campaign.archived_at.is_(None))

    # 3) פילטר חיפוש חופשי
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            Campaign.name.ilike(like),
            Campaign.audience.ilike(like),
            Campaign.goal.ilike(like),
        ))

    # 4) פילטר לפי ערוץ
    if ch and ch in VALID_CHANNELS:
        stmt = stmt.where(Campaign.default_channel == ch)

    # 5) מיון לפי עדכון אחרון/תאריך יצירה
    stmt = stmt.order_by(func.coalesce(Campaign.updated_at, Campaign.created_at).desc())

    # 6) שליפה והכנה לתבנית
    with SessionLocal() as db:
        rows = list(db.execute(stmt))
        campaigns = []
        for (c,) in rows:
            last_dt = c.updated_at or c.created_at or now_utc()
            campaigns.append({
                "id": c.id,
                "name": c.name,
                "audience": c.audience,
                "channel": c.default_channel,
                "goal": c.goal or "",
                "budget": c.budget or "",
                "created_at": (c.created_at or now_utc()).strftime("%Y-%m-%d %H:%M:%S"),
                "last_updated": last_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })

    return render_template(
        "index.html",
        title="CampAIgn",
        campaigns=campaigns,
        q=q, ch=ch,
        channels=sorted(list(VALID_CHANNELS)),
    )

@app.route("/campaign/new", methods=["GET", "POST"])
@login_required
def new_campaign():
    # טופס נוח למילוי חוזר במקרה של שגיאות
    form = {
        "name": (request.form.get("name") or "").strip(),
        "audience": (request.form.get("audience") or "").strip(),
        "channel": (request.form.get("channel") or "").strip(),
        "goal": (request.form.get("goal") or "").strip(),
        "budget": (request.form.get("budget") or "").strip(),
        "business_desc": (request.form.get("business_desc") or "").strip(),
        "landing_url": (request.form.get("landing_url") or "").strip(),
    }

    if request.method == "POST":
        errors = False
        if not form["name"]:
            flash("שם קמפיין הוא שדה חובה.", "error"); errors = True
        if not form["audience"]:
            flash("קהל יעד הוא שדה חובה.", "error"); errors = True
        if form["channel"] not in VALID_CHANNELS:
            flash("בחרי ערוץ תקין (Email / SMS / Social / Ads).", "error"); errors = True
        if form["budget"] and not form["budget"].replace(".", "", 1).isdigit():
            flash("תקציב חייב להיות מספר (ללא פסיקים).", "error"); errors = True

        if errors:
            return render_template("new_campaign.html", title="קמפיין חדש", form=form)

        # יצירה
        new_id = now_utc().strftime("%Y%m%d%H%M%S%f")
        with SessionLocal() as db:
            c = Campaign(
                id=new_id,
                name=form["name"],
                audience=form["audience"],
                default_channel=form["channel"],
                goal=form["goal"],
                budget=form["budget"] or "",
                business_desc=form["business_desc"] or "",
                landing_url=form["landing_url"] or "",
            )
            db.add(c)
            db.commit()

        flash("הקמפיין נוצר בהצלחה ✅", "success")
        return redirect(url_for("home"))

    # GET ראשוני
    return render_template("new_campaign.html", title="קמפיין חדש", form=form)

@app.route("/campaign/<campaign_id>/edit", methods=["GET", "POST"])
@login_required
def edit_campaign(campaign_id):
    """עריכת קמפיין קיים."""
    with SessionLocal() as db:
        row = db.execute(select(Campaign).where(Campaign.id == str(campaign_id))).first()
        if not row:
            abort(404)
        (c,) = row

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            audience = (request.form.get("audience") or "").strip()
            channel = (request.form.get("channel") or "").strip()
            goal = (request.form.get("goal") or "").strip()
            budget = (request.form.get("budget") or "").strip()
            business_desc = (request.form.get("business_desc") or "").strip() 
            landing_url = (request.form.get("landing_url") or "").strip()

            if not name:
                flash("שם קמפיין הוא שדה חובה.", "error")
                return redirect(url_for("edit_campaign", campaign_id=campaign_id))
            if not audience:
                flash("קהל יעד הוא שדה חובה.", "error")
                return redirect(url_for("edit_campaign", campaign_id=campaign_id))
            if channel not in VALID_CHANNELS:
                flash("בחרי ערוץ תקין (Email / SMS / Social / Ads).", "error")
                return redirect(url_for("edit_campaign", campaign_id=campaign_id))
            if budget and not budget.replace(".", "", 1).isdigit():
                flash("תקציב חייב להיות מספר.", "error")
                return redirect(url_for("edit_campaign", campaign_id=campaign_id))

            c.name = name
            c.audience = audience
            c.default_channel = channel
            c.goal = goal
            c.budget = budget or ""
            c.business_desc = business_desc 
            c.landing_url = landing_url
            db.commit()

            flash("הקמפיין עודכן בהצלחה ✅", "success")
            return redirect(url_for("home"))

        data = {
            "id": c.id,
            "name": c.name or "",
            "audience": c.audience or "",
            "channel": c.default_channel or "Email",
            "goal": c.goal or "",
            "budget": c.budget or "",
            "business_desc": c.business_desc or "",
            "landing_url": c.landing_url or "",
        }
    return render_template("edit_campaign.html",
                           title=f"עריכת קמפיין – {data['name']}",
                           c=data,
                           channels=sorted(list(VALID_CHANNELS)))

@app.route("/campaign/<campaign_id>/brief", methods=["GET", "POST"])
@login_required
def campaign_brief(campaign_id):
    """צפייה/יצירה של תקציר."""
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)
    path = brief_path(campaign_id)

    if request.method == "POST":
        text = generate_brief_text(camp)
        path.write_text(text, encoding="utf-8")
        touch_campaign(campaign_id)
        flash("התקציר נוצר ונשמר ✅", "success")
        return redirect(url_for("campaign_brief", campaign_id=campaign_id))

    brief_text = path.read_text(encoding="utf-8") if path.exists() else None
    return render_template("campaign_brief.html", title=f"תקציר – {camp.get('name','')}", campaign=camp, brief_text=brief_text)

@app.route("/campaign/<campaign_id>/brief/download")
@login_required
def download_brief(campaign_id):
    """הורדת קובץ תקציר."""
    path = brief_path(campaign_id)
    if not path.exists():
        flash("אין עדיין תקציר להורדה. צרי קודם תקציר.", "error")
        return redirect(url_for("campaign_brief", campaign_id=campaign_id))
    return send_file(path, as_attachment=True, download_name=f"brief_{campaign_id}.txt")

@app.route("/campaign/<campaign_id>/content", methods=["GET", "POST"])
@login_required
def campaign_content(campaign_id):
    """תוכן לפי ערוץ + Tone: יצירה/עריכה/הורדה + רעיונות, עם חובה לבחור ערוץ ידנית."""
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    # --- נרמול לערכים הקאנוניים: Email / SMS / Social / Ads ---
    def normalize_channel(val: str) -> str:
        raw = (val or "").strip()
        if raw in VALID_CHANNELS:
            return raw
        v = raw.lower()
        if v in {"email", "e-mail", "mail"}:
            return "Email"
        if v in {"sms", "text", "מסרון"}:
            return "SMS"
        if v in {"social", "socials", "facebook", "instagram", "tiktok", "רשת", "רשתות"}:
            return "Social"
        if v in {"ads", "ad", "מודעות", "פרסום"}:
            return "Ads"
        return ""

    selected_raw = (request.form.get("channel") or request.args.get("channel") or "").strip()
    selected     = normalize_channel(selected_raw)
    tone         = (request.form.get("tone") or request.args.get("tone") or "professional").strip().lower()

    content_txt = None
    if selected:
        path = content_path(campaign_id, selected)
        content_txt = path.read_text(encoding="utf-8") if path.exists() else None

    ideas = None

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action in {"generate", "ideas"} and not selected:
            flash("יש לבחור ערוץ לפני יצירה או הצגת רעיונות.", "error")
            return render_template("campaign_content.html",
                                   title=f"תוכן – {camp.get('name','')}",
                                   campaign=camp,
                                   selected_channel=selected,
                                   selected_tone=tone,
                                   content_txt=content_txt,
                                   ideas=None)

        if action == "ideas":
            ideas = generate_channel_ideas(camp, selected, tone=tone, n=3)
            flash("נוצרו רעיונות לתוכן. אפשר להכניס לעורך בלחיצה.", "success")
            return render_template("campaign_content.html",
                                   title=f"תוכן ({selected or '—'}) – {camp.get('name','')}",
                                   campaign=camp,
                                   selected_channel=selected,
                                   selected_tone=tone,
                                   content_txt=content_txt,
                                   ideas=ideas)

        if action == "generate":
            content_txt = generate_channel_content(camp, selected, tone=tone)
            content_path(campaign_id, selected).write_text(content_txt, encoding="utf-8")
            touch_campaign(campaign_id)
            flash("התוכן נוצר ונשמר ✅", "success")
            return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=selected, tone=tone))

        if action == "save":
            edited = (request.form.get("content_text") or "").strip()
            if not edited:
                flash("אי אפשר לשמור תוכן ריק.", "error")
            else:
                if selected:
                    content_path(campaign_id, selected).write_text(edited, encoding="utf-8")
                kind = selected.lower() if selected else ""
                if kind:
                    with SessionLocal() as db:
                        a = db.execute(
                            select(Asset).where(Asset.campaign_id == campaign_id, Asset.kind == kind)
                        ).scalar_one_or_none()
                        if a is None:
                            a = Asset(
                                id=f"{campaign_id}_{kind}",
                                campaign_id=campaign_id,
                                kind=kind,
                                content=edited,
                                updated_at=now_utc(),
                            )
                            db.add(a)
                        else:
                            a.content = edited
                            a.updated_at = now_utc()
                        db.commit()
                touch_campaign(campaign_id)
                flash("השינויים נשמרו ✅", "success")

            return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=selected, tone=tone))

    return render_template("campaign_content.html",
                           title=f"תוכן ({selected or '—'}) – {camp.get('name','')}",
                           campaign=camp,
                           selected_channel=selected,
                           selected_tone=tone,
                           content_txt=content_txt,
                           ideas=ideas)


@app.route("/campaign/<campaign_id>/ideas", methods=["GET", "POST"])
@login_required
def campaign_ideas(campaign_id):
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    selected = (request.values.get("channel") or camp.get("channel") or "Email").capitalize()
    tone = (request.values.get("tone") or "professional")

    if request.method == "POST":
        chosen = (request.form.get("chosen") or "").strip()
        if not chosen:
            flash("לא נבחר רעיון.", "error")
            return redirect(url_for("campaign_ideas", campaign_id=campaign_id, channel=selected, tone=tone))

        # שמירה כ"תוכן" לאותו ערוץ
        path = content_path(campaign_id, selected)
        path.write_text(chosen, encoding="utf-8")

        # שמירה ל-DB (assets) אם מוגדר
        try:
            kind = selected.lower()
            with SessionLocal() as db:
                a = db.execute(
                    select(Asset).where(Asset.campaign_id == campaign_id, Asset.kind == kind)
                ).scalar_one_or_none()
                if a is None:
                    a = Asset(
                        id=f"{campaign_id}_{kind}",
                        campaign_id=campaign_id,
                        kind=kind,
                        content=chosen,
                        updated_at=now_utc(),
                    )
                    db.add(a)
                else:
                    a.content = chosen
                    a.updated_at = now_utc()
                db.commit()
        except Exception:
            pass

        try:
            touch_campaign(campaign_id)  # אם יש לך את הפונקציה הזו
        except Exception:
            pass

        flash("הרעיון נשמר כתוכן הקמפיין ✅", "success")
        return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=selected, tone=tone))

    # GET — הצגת רעיונות
    ideas = generate_channel_ideas(camp, selected, tone, n=5)
    return render_template(
        "campaign_ideas.html",
        title=f"רעיונות – {camp.get('name','')}",
        campaign=camp,
        selected_channel=selected,
        selected_tone=tone,
        ideas=ideas,
    )

@app.route("/campaign/<campaign_id>/content/download")
@login_required
def download_content(campaign_id):
    """הורדת תוכן TXT לערוץ שנבחר."""
    channel = (request.args.get("channel") or "Email").capitalize()
    path = content_path(campaign_id, channel)
    if not path.exists():
        flash("אין עדיין תוכן להורדה לערוץ שנבחר.", "error")
        return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=channel))
    return send_file(path, as_attachment=True, download_name=f"content_{campaign_id}_{channel.lower()}.txt")

@app.route("/campaign/<campaign_id>/generate_all", methods=["GET", "POST"])
@login_required
def generate_all(campaign_id):
    # אם מנסים לגשת ב-GET לראוט שהוא פעולה, נחזור בעדינות למסך תוצאות
    if request.method != "POST":
        flash("פעולה זו מופעלת מכפתור (POST).", "error")
        return redirect(url_for("campaign_results", campaign_id=campaign_id))

    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    # Brief
    brief_path(campaign_id).write_text(generate_brief_text(camp), encoding="utf-8")

    # All channels
    for ch in ["Email", "SMS", "Social", "Ads"]:
        content_path(campaign_id, ch).write_text(
            generate_channel_content(camp, ch, tone="professional"),
            encoding="utf-8"
        )

    flash("נוצרו תקציר ותכנים לכל הערוצים ✅", "success")
    touch_campaign(campaign_id)
    return redirect(url_for("export_zip", campaign_id=campaign_id))

@app.route("/campaign/<campaign_id>/publish", methods=["GET", "POST"])
@login_required
def publish_content(campaign_id):
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    channel = (request.form.get("channel") or request.args.get("channel") or camp.get("channel") or "Email").capitalize()
    tone = (request.form.get("tone") or request.args.get("tone") or "professional")

    if request.method != "POST":
        flash("פרסום מופעל מכפתור (POST).", "error")
        return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=channel, tone=tone))

    # נטען/נייצר תוכן לערוץ
    path = content_path(campaign_id, channel)
    if not path.exists():
        content_txt = generate_channel_content(camp, channel, tone=tone)
        path.write_text(content_txt, encoding="utf-8")
    else:
        content_txt = path.read_text(encoding="utf-8")

    # נמענים: תחילה מה-CSV של הקמפיין; אם ריק ובמצב SendGrid ואין SENDGRID_TO — נעצור באלגנטיות
    recips = load_recipients(campaign_id)
    if (os.getenv("PUBLISH_MODE", "").lower() == "sendgrid") and not recips and not os.getenv("SENDGRID_TO"):
        flash("אין נמענים: לא הועלה קובץ נמענים לקמפיין וגם SENDGRID_TO לא מוגדר ב-.env.", "error")
        return redirect(url_for("campaign_recipients", campaign_id=campaign_id))

    # Publisher בהתאם ל-.env (SendGrid / Local)
    publisher = get_publisher()
    result = publisher.publish(
        campaign=camp,
        channel=channel,
        content=content_txt,
        to_emails=recips or None,  # ← אם לא הועלו נמענים – נשתמש בברירת המחדל מ-.env (SendGrid) או נשמור לוקאלית
    )

    if result.ok:
        touch_campaign(campaign_id)
        count_msg = f" לקהל של {result.recipients_count} נמענים" if getattr(result, "recipients_count", 0) else ""
        flash(f"✅ פורסם בהצלחה{count_msg}. {getattr(result, 'outfile', '')}", "success")
    else:
        flash(f"❌ פרסום נכשל: {result.message}", "error")

    return redirect(url_for("campaign_content", campaign_id=campaign_id, channel=channel, tone=tone))

@app.route("/campaign/<campaign_id>/export.zip")
@login_required
def export_zip(campaign_id):
    """מוריד ZIP עם Brief + כל התכנים הקיימים + meta."""
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # brief
        bp = brief_path(campaign_id)
        if bp.exists():
            zf.writestr(f"brief_{campaign_id}.txt", bp.read_text(encoding="utf-8"))
        # contents
        for ch in ["Email", "SMS", "Social", "Ads"]:
            cp = content_path(campaign_id, ch)
            if cp.exists():
                zf.writestr(f"content_{campaign_id}_{ch.lower()}.txt", cp.read_text(encoding="utf-8"))
        # metadata
        meta = (
            f"Campaign: {camp.get('name','')}\n"
            f"Audience: {camp.get('audience','')}\n"
            f"Channel(default): {camp.get('channel','')}\n"
            f"Goal: {camp.get('goal','')}\n"
            f"Budget: {camp.get('budget','')}\n"
            f"Exported at: {now_utc().isoformat(timespec='seconds')}Z\n"
            f"Landing URL: {camp.get('landing_url','')}\n"
        )
        zf.writestr(f"campaign_{campaign_id}_meta.txt", meta)

    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name=f"campaign_{campaign_id}_export.zip", mimetype="application/zip")

@app.route("/campaign/<campaign_id>/results")
@login_required
def campaign_results(campaign_id):
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    def mk_entry(kind, label, path: Path, download_url=None, channel=None):
        exists = path.exists()
        preview = None
        mtime = None
        size = None
        if exists:
            try:
                text = path.read_text(encoding="utf-8")
                preview = text[:800] + ("…" if len(text) > 800 else "")
            except Exception:
                preview = "(לא ניתן להציג תצוגה מקדימה)"
            st = path.stat()
            from datetime import datetime as _dt
            mtime = _dt.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size = st.st_size
        return {
            "kind": kind,           # "brief" / "content"
            "label": label,         # טקסט ידידותי
            "exists": exists,
            "channel": channel,     # Email/SMS/Social/Ads או None
            "download_url": download_url,
            "preview": preview,
            "mtime": mtime,
            "size": size,
        }

    files = []
    # תקציר
    bp = brief_path(campaign_id)
    files.append(mk_entry("brief", "תקציר", bp, url_for("download_brief", campaign_id=campaign_id)))

    # תכנים לכל הערוצים
    for ch in ["Email", "SMS", "Social", "Ads"]:
        cp = content_path(campaign_id, ch)
        files.append(mk_entry("content", f"תוכן – {ch}", cp,
                              url_for("download_content", campaign_id=campaign_id, channel=ch),
                              channel=ch))

    return render_template("results.html",
                           title=f"תוצאות – {camp.get('name','')}",
                           campaign=camp,
                           files=files,
                           summary=results_summary(campaign_id))

@app.route("/demo_mode", methods=["GET", "POST"])
@login_required
def demo_mode():
    if request.method != "POST":
        flash("מצב הדגמה מופעל מכפתור (POST).", "error")
        return redirect(url_for("home"))

    demo_id = now_utc().strftime("%Y%m%d%H%M%S%f")
    with SessionLocal() as db:
        demo = Campaign(
            id=demo_id,
            name="קמפיין הדגמה – CampAIgn",
            audience="לקוחות SMB בישראל",
            default_channel="Email",
            goal="הרשמה לוובינר",
            budget="1500"
        )
        db.add(demo)
        db.commit()

    camp_dict = get_campaign(demo_id)
    # Brief + channels
    brief_path(demo_id).write_text(generate_brief_text(camp_dict), encoding="utf-8")
    for ch in ["Email", "SMS", "Social", "Ads"]:
        content_path(demo_id, ch).write_text(generate_channel_content(camp_dict, ch, tone="professional"), encoding="utf-8")

    touch_campaign(demo_id)
    flash("נוצר קמפיין דמו ותוצרים לכל הערוצים ✅", "success")
    return redirect(url_for("export_zip", campaign_id=demo_id))

@app.route("/campaign/<campaign_id>/delete", methods=["POST"])
def delete_campaign(campaign_id):
    with SessionLocal() as db:
        row = db.execute(select(Campaign).where(Campaign.id == str(campaign_id))).first()
        if not row:
            flash("הקמפיין לא נמצא.", "error")
            return redirect(url_for("home"))
        (c,) = row
        # מחיקה רכה: סימון תאריך ארכוב — נשמר ב־DB אך לא מוצג ברשימה
        c.archived_at = now_utc()
        db.commit()

    flash("הקמפיין הועבר לארכיון (נשמר ב־DB).", "success")
    return redirect(url_for("home"))

@app.route("/campaign/<campaign_id>/view", endpoint="campaign_details")
@login_required
def campaign_details(campaign_id):
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    # שליפת updated_at אמיתי:
    with SessionLocal() as db:
        row = db.execute(select(Campaign).where(Campaign.id == str(campaign_id))).first()
        last_updated = None
        if row:
            (c,) = row
            last_dt = c.updated_at or c.created_at or now_utc()
            last_updated = last_dt.strftime("%Y-%m-%d %H:%M:%S")

    def mk_entry(kind, label, path: Path, download_url=None, channel=None):
        exists = path.exists()
        preview, mtime, size = None, None, None
        if exists:
            try:
                text = path.read_text(encoding="utf-8")
                preview = text[:800] + ("…" if len(text) > 800 else "")
            except Exception:
                preview = "(לא ניתן להציג תצוגה מקדימה)"
            st = path.stat()
            from datetime import datetime as _dt
            mtime = _dt.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size = st.st_size
        return {
            "kind": kind,
            "label": label,
            "exists": exists,
            "channel": channel,
            "download_url": download_url,
            "preview": preview,
            "mtime": mtime,
            "size": size,
        }

    files = []
    bp = brief_path(campaign_id)
    files.append(mk_entry("brief", "תקציר", bp, url_for("download_brief", campaign_id=campaign_id)))
    for ch in ["Email", "SMS", "Social", "Ads"]:
        cp = content_path(campaign_id, ch)
        files.append(mk_entry("content", f"תוכן – {ch}", cp,
                              url_for("download_content", campaign_id=campaign_id, channel=ch),
                              channel=ch))

    return render_template(
        "campaign_details.html",
        title=f"פרטי קמפיין – {camp.get('name','')}",
        campaign=camp,
        last_updated=last_updated or camp.get("created_at"),
        files=files
    )

@app.route("/campaign/<campaign_id>/publish_all", methods=["GET", "POST"], endpoint="publish_all")
@login_required
def publish_all_channels(campaign_id):
    if request.method != "POST":
        flash("פרסום כל הערוצים מופעל מכפתור (POST).", "error")
        return redirect(url_for("campaign_results", campaign_id=campaign_id))

    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    # נמענים לקמפיין (אופציונלי). אם אין—SendGrid ייקח מ-.env, Local רק יתעד JSON.
    recips = load_recipients(campaign_id)
    if (os.getenv("PUBLISH_MODE", "").lower() == "sendgrid") and not recips and not os.getenv("SENDGRID_TO"):
        flash("אין נמענים: לא הועלה קובץ נמענים לקמפיין וגם SENDGRID_TO לא מוגדר ב-.env.", "error")
        return redirect(url_for("campaign_recipients", campaign_id=campaign_id))

    publisher = get_publisher()
    results = []
    for ch in ["Email", "SMS", "Social", "Ads"]:
        # הבטחת תוכן קיים
        p = content_path(campaign_id, ch)
        if not p.exists():
            txt = generate_channel_content(camp, ch, tone="professional")
            p.write_text(txt, encoding="utf-8")
        else:
            txt = p.read_text(encoding="utf-8")

        res = publisher.publish(
            campaign=camp,
            channel=ch,
            content=txt,
            to_emails=recips or None,  # ← שימוש בנמענים שהעלית (או ברירת מחדל)
        )
        results.append((ch, res.ok, getattr(res, "outfile", None), getattr(res, "message", ""), getattr(res, "recipients_count", 0)))

    touch_campaign(campaign_id)
    ok = sum(1 for _, ok, _, _, _ in results if ok)
    fail = len(results) - ok
    if fail == 0:
        total = sum(rc for *_, rc in results)
        flash(f"✅ פורסמו בהצלחה כל הערוצים ({ok}/4). סה״כ נמענים: {total or '—'}", "success")
    else:
        msgs = "; ".join(f"{ch}: {'OK' if ok else 'FAIL'}" for ch, ok, *_ in results)
        flash(f"⚠️ חלקית: {ok}/4 הצליחו. פירוט: {msgs}", "error")

    return redirect(url_for("campaign_results", campaign_id=campaign_id))

@app.route("/campaign/<campaign_id>/recipients", methods=["GET", "POST"])
@login_required
def campaign_recipients(campaign_id):
    camp = get_campaign(campaign_id)
    if not camp:
        abort(404)

    path = recipients_path(campaign_id)
    existing = []
    if path.exists():
        try:
            existing = parse_emails(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    if request.method == "POST":
        # מקבלים קובץ CSV או טקסט חופשי
        file = request.files.get("file")
        text = (request.form.get("emails_text") or "").strip()
        if file and file.filename:
            try:
                text = file.read().decode("utf-8", "ignore")
            except Exception:
                flash("לא ניתן לקרוא את הקובץ. ודאי שהוא UTF-8.", "error")
                return redirect(url_for("campaign_recipients", campaign_id=campaign_id))

        emails = parse_emails(text)
        if not emails:
            flash("לא נמצאו אימיילים תקינים. אפשר להדביק טקסט או להעלות CSV.", "error")
            return redirect(url_for("campaign_recipients", campaign_id=campaign_id))

        # נשמור CSV תקני: שורה לכל אימייל
        path.write_text("\n".join(emails) + "\n", encoding="utf-8")
        flash(f"נשמרו {len(emails)} נמענים לקמפיין.", "success")
        return redirect(url_for("campaign_recipients", campaign_id=campaign_id))

    # GET
    sample = existing[:10]
    mtime = None
    if path.exists():
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    return render_template(
        "recipients.html",
        title=f"נמענים – {camp.get('name','')}",
        campaign=camp,
        count=len(existing),
        sample=sample,
        last_updated=mtime,
        has_file=path.exists(),
    )

# --- Error handlers: עדינים ולא מציפים הודעות ---

@app.errorhandler(403)
def _forbidden(_e):
    # לא סטטי/אייקון
    if (request.path or "").startswith("/static/") or request.path == "/favicon.ico":
        return ("", 403)
    flash("אין הרשאה לפעולה הזו. חזרנו למסך הבית.", "error")
    return redirect(url_for("home"))

@app.errorhandler(405)
def _method_not_allowed(_e):
    p = (request.path or "")
    # אל תצעקי על סטטיים/פאביקון
    if p.startswith("/static/") or p == "/favicon.ico":
        return ("", 405)
    flash("הפעולה הזו מופעלת מכפתור (POST).", "error")
    # נסה להחזיר למסך רלוונטי של קמפיין
    parts = p.strip("/").split("/")
    cid = parts[1] if len(parts) > 1 and parts[0] == "campaign" else None
    if cid:
        for ep in ("campaign_results", "campaign_content"):
            try:
                return redirect(url_for(ep, campaign_id=cid))
            except Exception:
                pass
    return redirect(url_for("home"))

@app.errorhandler(404)
def _not_found(_e):
    p = (request.path or "")
    # 1) לא מציפות הודעה על סטטיים/פאביקון
    if p.startswith("/static/") or p == "/favicon.ico":
        return ("", 404)

    # 2) בקשות JSON: מחזירות 404 רגיל בלי פלאש
    if request.accept_mimetypes.best == "application/json":
        return {"error": "not found"}, 404

    # 3) אם כבר בבית – לא נעשה redirect-לופ
    if request.endpoint == "home":
        return render_template("404.html"), 404 if "render_template" in globals() else ("", 404)

    flash("הדף לא נמצא. חזרנו למסך הבית.", "error")
    return redirect(url_for("home"))

# favicon: אם אין קובץ, אל תייצרי הודעות
@app.route("/favicon.ico")
def _favicon():
    f = BASE_DIR / "static" / "favicon.ico"
    if f.exists():
        return send_file(f, mimetype="image/x-icon")
    return ("", 204)  # No Content

@app.errorhandler(500)
def _server_error(_e):
    flash("שגיאת שרת. נסי שוב, ואם חוזר — ספרי לי.", "error")
    return redirect(url_for("home"))

# --- Guard: אם נכנסים ב-GET לנתיבי POST-only, מחזירים באלגנטיות ---
POST_ONLY_SUFFIXES = ("/delete", "/publish", "/publish_all", "/generate_all", "/demo_mode")

@app.before_request
def _guard_get_for_actions():
    if request.method != "GET":
        return
    p = (request.path or "").rstrip("/")
    if not p:
        return
    if p.endswith(POST_ONLY_SUFFIXES):
        flash("הפעולה הזו מופעלת מכפתור (POST).", "error")
        parts = p.strip("/").split("/")
        cid = parts[1] if len(parts) > 1 and parts[0] == "campaign" else None
        if cid:
            try:
                return redirect(url_for("campaign_results", campaign_id=cid))
            except Exception:
                try:
                    return redirect(url_for("campaign_content", campaign_id=cid))
                except Exception:
                    pass
        return redirect(url_for("home"))

@app.route("/integration/check_email", methods=["GET", "POST"], endpoint="check_email_integration")
@login_required
def check_email_integration():
    if request.method != "POST":
        flash("בדיקה מופעלת מכפתור (POST).", "error")
        return redirect(url_for("home"))

    if get_publish_mode() != "SendGrid":
        flash("מצב הפרסום אינו SendGrid. בדקי .env והפעילי מחדש.", "error")
        return redirect(url_for("home"))

    try:
        import requests, re
    except Exception:
        flash("חסר 'requests'. הריצי: python -m pip install requests", "error")
        return redirect(url_for("home"))

    api_key    = (os.getenv("SENDGRID_API_KEY") or "").strip()
    from_email = (os.getenv("SENDGRID_FROM") or "").strip()
    to_env     = (os.getenv("SENDGRID_TO") or "").strip()

    # בניית רשימת נמענים:
    # 1) אם יש SENDGRID_TO ב-.env — נפרק לפסיקים/רווחים/נקודה-פסיק
    # 2) אחרת נשתמש באימייל של המשתמש המחובר (session)
    # 3) ואם גם זה לא קיים — נשתמש ב-from_email
    emails = []
    if to_env:
        emails = [e.strip() for e in re.split(r"[,\s;]+", to_env) if e.strip()]
    if not emails:
        user = session.get("user") or {}
        if user.get("email"):
            emails = [user["email"].strip()]
    if not emails and from_email:
        emails = [from_email]

    if not from_email:
        flash("SENDGRID_FROM לא מוגדר/ריק ב-.env. הגדירי שולח מאומת ב-SendGrid.", "error")
        return redirect(url_for("home"))
    if not emails:
        flash("אין נמענים לבדיקה: הגדירי SENDGRID_TO או התחברי עם אימייל תקין.", "error")
        return redirect(url_for("home"))

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "personalizations": [{"to": [{"email": e} for e in emails]}],
        "from": {"email": from_email},
        "subject": "CampAIgn – Email Integration Check (Sandbox)",
        "content": [{"type": "text/plain", "value": "This is a sandbox verification. No email was delivered."}],
        "mail_settings": {"sandbox_mode": {"enable": True}},  # ← לא שולח בפועל
    }

    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        if r.status_code in (200, 202):
            flash(f"✅ SendGrid Sandbox תקין. from={from_email}, to={', '.join(emails)}", "success")
        else:
            msg = (r.text or "")[:300].replace("\n", " ")
            flash(f"❌ SendGrid {r.status_code}: {msg}", "error")
    except Exception as e:
        flash(f"❌ שגיאת רשת: {e}", "error")

    return redirect(url_for("home"))

# ---------- Main ----------

if __name__ == "__main__":
    app.run(debug=True)
