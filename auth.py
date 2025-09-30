# auth.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps

auth_bp = Blueprint("auth", __name__)

# נתיבים שמקבלים רק POST (או פעולות מסוכנות) — לא חוזרים אליהם אחרי login
POST_ONLY_SUFFIXES = ("/delete", "/publish", "/publish_all", "/generate_all")
POST_ONLY_EXACT = {"/demo_mode"}

def _is_post_only_path(path: str) -> bool:
    if not path:
        return False
    if path in POST_ONLY_EXACT:
        return True
    # פעולות תחת /campaign/<id>/... שמסתיימות באחת הסיומות
    if path.startswith("/campaign/") and path.endswith(POST_ONLY_SUFFIXES):
        return True
    return False

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            # אם זו בקשת GET – חזרי לכתובת עצמה; אם לא (POST) – חזרי לעמוד שהגיע ממנו (referrer) או לבית
            next_target = request.path if request.method == "GET" else (request.referrer or url_for("home"))
            # לעולם לא מפנים לנתיב POST-only
            if _is_post_only_path(next_target):
                next_target = url_for("home")
            flash("יש להתחבר כדי להמשיך.", "error")
            return redirect(url_for("auth.login", next=next_target))
        return view(*args, **kwargs)
    return wrapped

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            # אם הבקשה הנוכחית היא לא GET (כלומר כפתור POST), הפני חזרה ל-referrer או לבית
            next_target = request.path
            if request.method != "GET":
                next_target = request.referrer or url_for("home")
            flash("יש להתחבר כדי להמשיך.", "error")
            return redirect(url_for("auth.login", next=next_target))
        return view(*args, **kwargs)
    return wrapped

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # תמיכה ב-next גם ב-GET וגם ב-POST
    next_url = request.values.get("next", "")
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        if not name or not email:
            flash("יש למלא שם ואימייל.", "error")
            return render_template("login.html", title="התחברות/הרשמה", next=next_url)
        session["user"] = {"name": name, "email": email}
        flash(f"מחוברת כ־{name}.", "success")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("home"))
    return render_template("login.html", title="התחברות/הרשמה", next=next_url)

@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    flash("התנתקת בהצלחה.", "success")
    return redirect(url_for("home"))
