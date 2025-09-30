from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class PublishResult:
    ok: bool
    outfile: str | None = None
    message: str | None = None
    recipients_count: int | None = None


class LocalFilePublisher:
    """
    מפרסם לוקאלית: שומר את “השליחה” כ-JSON בתיקייה נתונה (לשימוש בדמו/בדיקות).
    """
    def __init__(self, outdir: Path):
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        campaign: dict,
        channel: str,
        content: str,
        to_emails: Optional[Iterable[str]] = None,
    ) -> PublishResult:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        fname = f"{campaign['id']}_{channel.lower()}_{ts}.json"

        payload = {
            "campaign_id": campaign.get("id"),
            "campaign_name": campaign.get("name"),
            "channel": channel,
            "sent_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "content": content,
            "recipients": list(to_emails or []),
            "meta": {
                "audience": campaign.get("audience"),
                "goal": campaign.get("goal"),
                "budget": campaign.get("budget"),
                "business_desc": campaign.get("business_desc"),
                "landing_url": campaign.get("landing_url"),
            },
        }

        out_path = self.outdir / fname
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return PublishResult(
            ok=True,
            outfile=str(out_path),
            recipients_count=len(payload["recipients"]),
        )


class SendGridEmailPublisher:
    """
    שולח אימייל אמיתי דרך SendGrid API.

    דרישות סביבה:
      - SENDGRID_API_KEY
      - SENDGRID_FROM (דומיין/שולח מאומת ב-SendGrid)

    ברירת מחדל לנמענים יכולה להגיע מ-SENDGRID_TO (מופרד בפסיקים),
    אך אפשר גם להעביר override ב־publish(..., to_emails=[...]).
    """
    def __init__(self, api_key: str, from_email: str, to_email_default: str | None = None):
        self.api_key = (api_key or "").strip()
        self.from_email = (from_email or "").strip()
        self.default_to: List[str] = []
        if to_email_default:
            self.default_to = [e.strip() for e in to_email_default.split(",") if e.strip()]

    # --- עזר פנימי: נירמול URL (הוספת https:// אם חסר) ---
    @staticmethod
    def _normalize_url(url: str | None) -> str | None:
        if not url:
            return None
        u = url.strip()
        if not u:
            return None
        if not re.match(r"^https?://", u, flags=re.IGNORECASE):
            u = "https://" + u
        return u

    def publish(
        self,
        campaign: dict,
        channel: str,
        content: str,
        to_emails: Optional[Iterable[str]] = None,
    ) -> PublishResult:
        # תלות “רכה” כדי לא להפיל את השרת בעת import הקובץ
        try:
            import requests
        except Exception:
            return PublishResult(ok=False, message="חסר תלות: requests. הריצי: python -m pip install requests")

        # אימות פרמטרים בסיסי
        if not self.api_key or not self.from_email:
            return PublishResult(ok=False, message="חסרים SENDGRID_API_KEY / SENDGRID_FROM בסביבה")

        recipients = list(dict.fromkeys(list(to_emails or self.default_to)))  # ייחוד קשיח
        if not recipients:
            return PublishResult(ok=False, message="אין נמענים לשליחה (הרשימה ריקה).")

        # נושא/גוף – בסיס
        subject = (campaign.get("name") or "CampAIgn").strip()
        body = (content or "").strip() or "(ריק)"

        # חילוץ Subject/Preheader אם הוזנו בתחילת הטקסט
        try:
            lines = body.splitlines()

            # Subject:
            subj_idx = next((i for i, ln in enumerate(lines[:5]) if ln.strip().lower().startswith("subject:")), None)
            if subj_idx is not None:
                subj_val = lines[subj_idx].split(":", 1)[1].strip()
                if subj_val:
                    subject = subj_val
                del lines[subj_idx]
                body = "\n".join(lines).lstrip()

            lines = body.splitlines()
            pre_idx = next((i for i, ln in enumerate(lines[:5]) if ln.strip().lower().startswith("preheader:")), None)
            if pre_idx is not None:
                pre_val = lines[pre_idx].split(":", 1)[1].strip()
                lines[pre_idx] = pre_val
                body = "\n".join(lines).lstrip()
        except Exception:
            # לא מעכב שליחה — נתעלם מניסיונות חילוץ שנכשלו
            pass
                # --- Subject: לנקות כל תג בסוגריים מרובעים (כולל [Email]) ולהוסיף [פרסומת] פעם אחת ---
        raw_subject = subject  # למעקב
        # מסיר *כל* תג בסוגריים מרובעים מכל מקום בכותרת (עד 30 תווים בין הסוגריים)
        clean_subject = re.sub(r"\s*\[[^\]]{1,30}\]\s*", " ", raw_subject)
        clean_subject = re.sub(r"\s{2,}", " ", clean_subject).strip()
        subject = f"[פרסומת] {clean_subject}".strip()

        # (לוג דיבאג – תראי בקונסול מה באמת נשלח)
        print(f"[CampAIgn] Subject cleaned: raw='{raw_subject}' -> final='{subject}'", flush=True)

                # --- ניקוי subject מכל תג בסוגריים מרובעים (כולל [Email]/[SMS]) בכל מקום בכותרת ---
        subject = re.sub(r"\s*\[[^\]]{1,30}\]\s*", " ", subject).strip()
        subject = re.sub(r"\s{2,}", " ", subject)

        # --- FINAL email normalization (subject + body) ---
        if channel and channel.lower() == "email":
            # Subject: נקה *כל* תג בסוגריים מרובעים מכל מקום (כולל [Email]), ואז הוסף [פרסומת] פעם אחת
            subject = re.sub(r"\s*\[[^\]]{1,30}\]\s*", " ", subject)
            subject = re.sub(r"\s{2,}", " ", subject).strip()
            if not subject.startswith("[פרסומת]"):
                subject = f"[פרסומת] {subject}"

            # נירמול לינק מדף הנחיתה (אם שכחת https:// נוסיף)
            def _norm(u: str | None) -> str | None:
                if not u:
                    return None
                u = u.strip()
                if not u:
                    return None
                if not re.match(r"^https?://", u, re.I):
                    u = "https://" + u
                return u
            link = _norm(campaign.get("landing_url")) or "https://example.com"

            # גוף: הסרת תגיות מרובעות בכל מקום (כמו [דחיפות], [פתיח חד] וכו')
            body = re.sub(r"\[[^\]\n]{1,30}\]", "", body)

            # החלפת מצייני קישור נפוצים בלינק (כולל RTL)
            placeholders = [
                r"\[קישור\]", r"<קישור קצר>", r"‪<קישור קצר>‬",
                r"\[link\]", r"\[LINK\]", r"<short link>",
            ]
            for ph in placeholders:
                body = re.sub(ph, link, body, flags=re.I)

            # אם עדיין אין URL ובטקסט מופיעה המילה 'קישור' (מילה בודדת) – נחליף אותה בלינק
            if not re.search(r"https?://", body, flags=re.I):
                body = re.sub(r"(?<!\w)קישור(?!\w)", link, body)

            # ואם עדיין אין URL בכלל – נוסיף בסוף
            if not re.search(r"https?://", body, flags=re.I):
                body = body.rstrip() + f"\n\nקישור: {link}"

            # ניקוי רווחים/שורות ריקות
            body = re.sub(r"[ \t]{2,}", " ", body)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()

            # Debug לשרת (לא בדפדפן)
            print(f"[CampAIgn] FINAL SUBJECT: {subject} | LINK USED: {link}", flush=True)

        # בניית בקשת ה־API של SendGrid
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = {
            "personalizations": [{"to": [{"email": e} for e in recipients]}],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }

        # שליחה
        try:
            r = requests.post(url, headers=headers, json=data, timeout=20)
        except Exception as e:
            return PublishResult(ok=False, message=f"שגיאת רשת: {e}", recipients_count=len(recipients))

        if r.status_code in (200, 202):
            return PublishResult(
                ok=True,
                outfile=f"sendgrid://{len(recipients)}",
                recipients_count=len(recipients),
            )

        # שגיאה מה-API
        msg = (r.text or "").strip().replace("\n", " ")
        return PublishResult(
            ok=False,
            message=f"SendGrid {r.status_code}: {msg[:300]}",
            recipients_count=len(recipients),
        )
