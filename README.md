CampAIgn

מחולל קמפיינים קטן לעסקים קטנים: הפקת בריף ותוכן פרסומי (Email / SMS / Social / Ads) בעזרת OpenAI, ניהול קמפיינים, יצוא ZIP, ופרסום מיילים דרך SendGrid. ממשק RTL, טון כתיבה, רעיונות, ארכוב/מחיקה ידידותיים.

✨ יכולות

יצירה/עריכה של קמפיינים (שם, קהל יעד, מטרה, ערוץ, תקציב, תיאור עסק, לינק נחיתה).

בריף + תוכן לפי ערוץ, כולל Tone (מקצועי/ידידותי/חד/הומוריסטי/רשמי).

כפתור “רעיונות” שמחזיר כמה הצעות קצרות לשילוב מהיר.

הורדות: TXT לכל ערוץ + ZIP מרוכז.

פרסום מיילים בעזרת SendGrid או “פרסום” לוקלי (JSON).

חיפוש/סינון קמפיינים, דף תוצאות, ארכוב בקשה למחיקה (לא מוחק מה-DB).

RTL מלא, רכיבי UI עקביים, התראות (flash).

🧱 טכנולוגיות

Python + Flask + Jinja2

SQLAlchemy + SQLite

OpenAI API (אופציונלי—נופל לפולבק איכותי אם אין מפתח)

SendGrid (אופציונלי)

HTML/CSS/Vanilla JS

📦 מבנה תיקיות (עיקרי)

CampAIgn/
├─ app.py
├─ auth.py
├─ db.py
├─ models.py
├─ publishers.py
├─ requirements.txt
├─ .env.example
├─ data/
│  ├─ briefs/
│  ├─ content/
│  ├─ published/
│  └─ recipients/
│     └─ sample.csv
└─ templates/ & static/

🔐 משתני סביבה (.env)

צור קובץ .env מקומי לפי הדוגמה:

# Flask
SECRET_KEY=change-me-in-production

# OpenAI (אופציונלי — משפר את האיכות; אם חסר יש פולבק איכותי)
OPENAI_API_KEY=sk-...

# פרסום מיילים
PUBLISH_MODE=local          # sendgrid / local
SENDGRID_API_KEY=           # חובה אם sendgrid
SENDGRID_FROM=              # כתובת מאומתת ב-SendGrid
SENDGRID_TO=                # אופציונלי: רשימת נמענים לפיילוט, מופרד בפסיקים

# לוגיקה (אופציונלי)
DEFAULT_TONE=professional

Recipients CSV: ניתן גם להעלות רשימת נמענים ל־data/recipients/recipients.csv בעמודה אחת email

דוגמה (data/recipients/sample.csv):

email
demo@example.com

▶️ הרצה מקומית

python3 -m venv venv
source venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

# קובץ .env בקור הגיט (העתיקי את .env.example וערכי)
flask --app app run --debug
# http://127.0.0.1:5000

📮 בדיקת SendGrid (אופציונלי)

ודאי שב־.env מוגדרים: PUBLISH_MODE=sendgrid, SENDGRID_API_KEY, SENDGRID_FROM.

התחברי באפליקציה → במסך הראשי לחצי “בדיקת אימייל” (אם הוספת את הכפתור) או הריצי את הנתיב:

POST ל־/integration/check_email (קיימת בדיקת sandbox שמוודאת שהמפתח פעיל).

פרסום אמיתי מתבצע ממסך התוכן/תוצאות (לא מצב sandbox).

🚀 הפצה לכתובת אמיתית (Render)

זו הדרך המהירה לקבל URL ציבורי (ללא Docker).

1) הכנת ריפו ב-GitHub

העלי את כל קבצי הפרויקט (ללא .env!).

ודאי שקיים:

requirements.txt (אם אין, צרי עם pip freeze > requirements.txt).

(מומלץ) runtime.txt עם גרסה יציבה של Python:

python-3.11.9


(מומלץ) Procfile (אופציונלי, ברנדר מסתדר גם בלי):

web: gunicorn app:app --preload --workers 2 --threads 4 --timeout 120


ודאי ש־gunicorn נמצא ב־requirements.txt.

2) Render: יצירת Web Service

כנסי ל־render.com
 → New → Web Service.

בחרי את הריפו מה-GitHub.

Build Command:

pip install -r requirements.txt


Start Command:

gunicorn app:app --preload --workers 2 --threads 4 --timeout 120


Environment: Python.

Environment Variables (Settings → Environment):

העבירי לשם את ה־.env (ללא שורות הערה).

מינימום: SECRET_KEY, ואם את מציגה פרסום: PUBLISH_MODE, SENDGRID_API_KEY, SENDGRID_FROM.

Persistent Disk (מומלץ מאוד! כדי לשמור קבצים/ZIPים בין דיפלואים):

Settings → Disks → Add Disk

Size: 1GB (מספיק)

Mount Path: /opt/render/project/src/data

זה חייב להתאים לנתיב DATA_DIR של האפליקציה (זה בדיוק ה־default בקוד).

פרסומי Build יופיעו בדשבורד; בסוף תקבלי URL ציבורי כמו:
https://campaign.onrender.com (שם אקראי/שם שתבחרי).

טיפ: אם יש שגיאת פורט/בינדינג—אין צורך להגדיר PORT; Gunicorn מטפל בזה ברנדר.

חלופות מהירות אחרות

Railway — תהליך דומה, הוספת Volume ל־/data.

PythonAnywhere — פחות DevOps, ממשק נוח להצגת Flask.

Fly.io — דורש Dockerfile ו־fly.toml, אבל נותן שליטה גבוהה.

🧪 בדיקות ידניות (לפני שמגישים)

התחברות → מסך קמפיינים → “קמפיין חדש” → שמירה.

בריף: “צור/רענן בריף” → הורדה → חזרה.

תוכן:

בחרי ערוץ + Tone → “רעיונות” → “השתמשי ברעיון” → “צור/רענן תוכן” → “שמור עריכה”.

“פרסום” (SendGrid) או פרסום מקומי (LocalFilePublisher).

תוצאות: הורדת ZIP, פתיחה לעריכה, פרסום.

מחיקה: דרופדאון → מחיקה → מודאל אישור → בדיקה שהקמפיין נעלם מהרשימה ונשמר ב־DB (archived).

RTL ו־UI: ודאי שהכפתורים בולטים (פרסום/הורדה/שמור).

🛡️ מצב Production

כבי Debug (app.run(debug=True) → רק בפיתוח).

SECRET_KEY חזק.

ניהול הרשאות בסיסי: מסכים רגישים מוגנים ב־@login_required.

❓שאלות נפוצות

אין לי API Key של OpenAI → הכל עובד; מחולל התוכן יפול לפולבק איכותי.

אין SendGrid → השאירי PUBLISH_MODE=local; הפרסום ייווצר כקבצי JSON ב־data/published/.

קבצים נעלמים אחרי דיפלוי → ודאי שהוספת Persistent Disk ומיפית ל־/opt/render/project/src/data.

## 🔌 Roadmap — אינטגרציות עתידיות

בגרסאות הבאות האפליקציה תכלול חיבורי פרסום נוספים מעבר ל-Email:

### SMS (לשליחת הודעות)
- **ספק מומלץ**: Twilio (או ספק חלופי תואם API).
- **תהליכים**:
  1) פתיחת חשבון וקבלת מספר שולח מאומת.  
  2) יצירת אישורי API (Account SID, Auth Token).  
  3) הוספת טפסי opt-in ונוסח opt-out (“להסרה השיבו ‘STOP’”).  
  4) התאמת הטקסטים להגבלת אורך (כ־160 תווים בהודעה).
- **ENV מתוכנן**:

SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM=+9725...

- **Publisher מתוכנן**: `TwilioSmsPublisher` עם ממשק אחיד `publish(campaign, channel, content, recipients)`.

### רשתות חברתיות (Instagram / Facebook / LinkedIn)
- **Meta (Instagram/Facebook)**: שימוש ב-Graph API.
- יצירת אפליקציה ב-Meta for Developers, קבלת `client_id/client_secret`.
- OAuth 2.0, קבלת הרשאות מתאימות (pages_manage_posts, instagram_basic/instagram_content_publish).
- בחירת יעד פרסום: דף פייסבוק (Page) או חשבון IG עסקי (Professional).
- **ENV מתוכנן**:
  ```
  META_APP_ID=...
  META_APP_SECRET=...
  META_REDIRECT_URI=https://your-domain.com/integration/meta/callback
  META_PAGE_ID=...
  IG_BUSINESS_ACCOUNT_ID=...
  ```
- **LinkedIn**: פרסום לפיד של דף חברה (Organization).
- אפליקציה ב-LinkedIn Developer Portal, OAuth 2.0.
- הרשאות `w_member_social` / `w_organization_social`.
- **ENV מתוכנן**:
  ```
  LINKEDIN_CLIENT_ID=...
  LINKEDIN_CLIENT_SECRET=...
  LINKEDIN_REDIRECT_URI=https://your-domain.com/integration/linkedin/callback
  LINKEDIN_ORG_ID=...
  ```
- **Publishers מתוכננים**:
- `MetaPublisher` (FB/IG), `LinkedInPublisher` — אותו ממשק `publish(...)` לצידם של `SendGridEmailPublisher`/`LocalFilePublisher`.
- **UX מתוכנן**:
- מסך “חיבורים” עם כפתורי **Connect** (OAuth) לכל פלטפורמה.
- בחירת יעד פרסום (Page/IG/Organization) מתוך dropdown לאחר התחברות.
- כפתור “תצוגה מקדימה” לפני פרסום.

### הערות תאימות ונגישות
- **SMS**: חובה opt-in, תיעוד opt-out, שמירת לוג, כיבוד בקשות הסרה.  
- **Social**: ייתכן צורך ב-App Review לקבלת הרשאות פרסום.  
- **אבטחת מידע**: אין לשמור `client_secret` בגיט. יש להצפין אסימונים ולרענן refresh tokens.  
- **פורמט תוכן**: המנוע מתאים טקסטים לכל ערוץ (אורך, שפה, אימוג’ים/האשטגים), כולל כללי חיתוך לגבולות תווים.

### דיזיין קוד (גבוה-רמה)
- **ממשק Publisher אחיד** לכל יעד: `publish(campaign, channel, content, recipients=None, extras=None) -> Result`.
- **Feature Flags** (עתידי): הפעלה/כיבוי לפי ENV, למשל:

PUBLISH_MODE=local # קיים
ENABLE_SMS=true
ENABLE_META=true
ENABLE_LINKEDIN=true

- **Callbacks**:
- נתיבי OAuth:  
  - `GET /integration/meta/connect` → `GET /integration/meta/callback`  
  - `GET /integration/linkedin/connect` → `GET /integration/linkedin/callback`  
- אחסון אסימונים: טבלת `integrations_tokens` (או קובץ מוצפן).

> שימו לב: חלק מהאינטגרציות דורשות בדיקות ואישורי Sandbox/Review מצד הפלטפורמות, ויכולות להוסיף תלויות חדשות ל־`requirements.txt`.
