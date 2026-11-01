from __future__ import annotations

import csv
import html
import io
import json
import math
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask, Response, flash, jsonify, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from civil_engineering import assess_network
from model_service import FEATURES, load_and_train

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATABASE = os.getenv("HYDROWATCH_DATABASE", str(ROOT / "hydrowatch.db"))
DATASET_PATH = ROOT / os.getenv(
    "HYDROWATCH_DATASET", "location_aware_gis_leakage_dataset_with_units.csv"
)
CIVIL_DATA_PATH = ROOT / "civil_data.csv"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=64 * 1024,
)

try:
    model, model_evaluation = load_and_train(DATASET_PATH)
    model_error = None
except ValueError as error:
    model = None
    model_evaluation = None
    model_error = str(error)


@contextmanager
def db():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL,
                source_mode TEXT NOT NULL,
                prediction INTEGER NOT NULL CHECK (prediction IN (0, 1)),
                leak_score REAL,
                readings_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_analyses_user_created
                ON analyses(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                analysis_id INTEGER NOT NULL UNIQUE REFERENCES analyses(id) ON DELETE CASCADE,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
                status_message TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_user_created
                ON alerts(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS pipeline_networks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            );
            CREATE TABLE IF NOT EXISTS civil_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                network_id INTEGER NOT NULL REFERENCES pipeline_networks(id) ON DELETE CASCADE,
                result_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_civil_user_created
                ON civil_assessments(user_id, created_at DESC);
        """)


init_db()


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.filters["from_json"] = json.loads


def valid_csrf() -> bool:
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("_csrf_token", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def csrf_protected(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not valid_csrf():
            if request.path.startswith("/api/") or request.is_json:
                return jsonify(error="Invalid or missing CSRF token."), 400
            flash("Your form expired. Please try again.", "error")
            return redirect(request.referrer or url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/") or request.path == "/predict":
                return jsonify(error="Login required.", login_required=True), 401
            return redirect(url_for("login"))
        if current_user() is None:
            session.clear()
            if request.path.startswith("/api/") or request.path == "/predict":
                return jsonify(error="Login required.", login_required=True), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    with db() as connection:
        return connection.execute(
            "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def require_model():
    if model is None:
        raise RuntimeError(model_error or "The leakage model is unavailable.")


def validate_readings(data: Any) -> dict[str, float]:
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    ranges = {
        "pressure": (0, 200), "flow": (0, 500), "temperature": (-50, 150),
        "vibration": (0, 20), "rpm": (0, 5000), "hours": (0, 100000),
    }
    readings = {}
    for field, (minimum, maximum) in ranges.items():
        value = data.get(field)
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be a finite number.") from error
        if not math.isfinite(numeric):
            raise ValueError(f"{field} must be a finite number.")
        if not minimum <= numeric <= maximum:
            raise ValueError(f"{field} must be between {minimum} and {maximum}.")
        readings[field] = numeric
    return readings


def read_mail_configuration() -> tuple[dict[str, Any] | None, str | None]:
    host = os.getenv("MAIL_SERVER", "").strip()
    username = os.getenv("MAIL_USERNAME", "").strip()
    password = os.getenv("MAIL_PASSWORD", "")
    sender = os.getenv("MAIL_DEFAULT_SENDER", username).strip()

    missing = [
        key for key, value in (
            ("MAIL_SERVER", host),
            ("MAIL_USERNAME", username),
            ("MAIL_PASSWORD", password.strip()),
            ("MAIL_DEFAULT_SENDER", sender),
        ) if not value
    ]
    if missing:
        return None, "Missing or empty SMTP setting(s): " + ", ".join(missing) + "."

    true_values = {"1", "true", "yes", "on"}
    false_values = {"0", "false", "no", "off"}
    booleans = {}
    for key, default in (("MAIL_USE_TLS", "true"), ("MAIL_USE_SSL", "false")):
        raw_value = os.getenv(key, default).strip().lower()
        if raw_value in true_values:
            booleans[key] = True
        elif raw_value in false_values:
            booleans[key] = False
        else:
            return None, f"{key} must be true or false."

    try:
        port = int(os.getenv("MAIL_PORT", "465" if booleans["MAIL_USE_SSL"] else "587"))
    except ValueError:
        return None, "MAIL_PORT must be a valid port number."
    if not 1 <= port <= 65535:
        return None, "MAIL_PORT must be between 1 and 65535."
    if booleans["MAIL_USE_SSL"] and booleans["MAIL_USE_TLS"]:
        return None, "Set only one transport mode: MAIL_USE_SSL=true or MAIL_USE_TLS=true."
    if not booleans["MAIL_USE_SSL"] and not booleans["MAIL_USE_TLS"]:
        return None, "Enable either MAIL_USE_SSL or MAIL_USE_TLS."
    return ({
        "host": host,
        "port": port,
        "username": username,
        "password": password.replace(" ", ""),
        "sender": sender,
        "tls": booleans["MAIL_USE_TLS"],
        "ssl": booleans["MAIL_USE_SSL"],
    }, None)


def mail_configuration():
    config, _error = read_mail_configuration()
    return config


def mail_configuration_error() -> str | None:
    _config, error = read_mail_configuration()
    return error


def deliver_email(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> tuple[str, str]:
    config = mail_configuration()
    if config is None:
        return "failed", mail_configuration_error() or "SMTP configuration is invalid."
    if not recipient.strip():
        return "failed", "Email alert recipient is unavailable."
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        smtp_context = ssl.create_default_context()
        if config["ssl"]:
            smtp = smtplib.SMTP_SSL(
                config["host"], config["port"], timeout=15, context=smtp_context
            )
        else:
            smtp = smtplib.SMTP(config["host"], config["port"], timeout=15)
        with smtp as connection:
            if config["tls"] and not config["ssl"]:
                connection.starttls(context=smtp_context)
            connection.login(config["username"], config["password"])
            refused = connection.send_message(message)
            if refused:
                return "failed", "Mail server refused delivery to the recipient."
    except smtplib.SMTPAuthenticationError as error:
        app.logger.warning("HydroWatch SMTP authentication failed (%s).", type(error).__name__)
        return "failed", (
            "SMTP authentication was rejected. Verify MAIL_USERNAME and MAIL_PASSWORD; "
            "for Gmail, use an app password."
        )
    except (OSError, smtplib.SMTPException) as error:
        app.logger.warning("HydroWatch SMTP delivery failed (%s).", type(error).__name__)
        return "failed", "Email delivery failed. Check SMTP configuration and retry."
    return "sent", "Mail server accepted the alert for delivery."


def alert_content(
    user_name: str,
    alert_id: int,
    analysis_id: int,
    created_at: str,
    score: float | None,
    readings: dict[str, float],
    source_mode: str,
) -> tuple[str, str]:
    base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    source_label = {
        "manual": "Manual user-entered readings (not live hardware measurements)",
        "demo": "Simulated demo readings (not live hardware measurements)",
    }.get(source_mode, "User-provided readings (not verified as live hardware measurements)")
    score_line = f"Model leak score: {score:.2f}% (uncalibrated classifier score)\n" if score is not None else ""
    text_body = (
        f"HydroWatch leakage alert\n\nHello {user_name},\n"
        f"Alert ID: {alert_id}\nAnalysis ID: {analysis_id}\n"
        f"Analysis timestamp: {created_at}\nPredicted status: Leak suspected\n"
        f"Reading source: {source_label}\n"
        f"{score_line}\nReadings:\n"
        f"Pressure: {readings['pressure']:.2f} PSI\nFlow: {readings['flow']:.2f} L/min\n"
        f"Temperature: {readings['temperature']:.2f} °C\n"
        f"Vibration: {readings['vibration']:.3f} mm/s\n"
        f"Pump speed: {readings['rpm']:.1f} RPM\n"
        f"Operational hours: {readings['hours']:.1f} h\n\n"
        "Recommended action: verify the readings, inspect nearby joints and valves, "
        "and arrange a field inspection. This software prediction is not proof of a physical leak.\n"
        f"Dashboard: {base_url}/\n"
    )
    safe_name = html.escape(user_name)
    safe_source = html.escape(source_label)
    safe_timestamp = html.escape(created_at)
    safe_dashboard = html.escape(base_url + "/", quote=True)
    score_row = (
        f"<tr><th>Model score</th><td>{score:.2f}% "
        "(uncalibrated classifier score)</td></tr>"
        if score is not None else ""
    )
    html_body = f"""\
<!doctype html>
<html lang="en"><body style="margin:0;padding:24px;background:#f3f7f6;font-family:Arial,sans-serif;color:#203d40">
  <main style="max-width:620px;margin:auto;padding:28px;background:#fff;border:1px solid #dfe8e5;border-radius:12px">
    <p style="margin:0 0 8px;color:#087a70;font-size:12px;font-weight:bold;letter-spacing:1px">HYDROWATCH · WATER NETWORK INTELLIGENCE</p>
    <h1 style="font-size:22px">Leak suspected</h1>
    <p>Hello {safe_name},</p>
    <p>The HydroWatch model classified this analysis as a possible leak. This is a software prediction and is not proof of a physical leak.</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Alert ID</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{alert_id}</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Analysis ID</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{analysis_id}</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Timestamp</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{safe_timestamp}</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Reading source</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{safe_source}</td></tr>
      {score_row}
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Pressure</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{readings['pressure']:.2f} PSI</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Flow rate</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{readings['flow']:.2f} L/min</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Temperature</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{readings['temperature']:.2f} °C</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Vibration</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{readings['vibration']:.3f} mm/s</td></tr>
      <tr><th align="left" style="padding:8px;border-bottom:1px solid #e5eeeb">Pump speed</th><td style="padding:8px;border-bottom:1px solid #e5eeeb">{readings['rpm']:.1f} RPM</td></tr>
      <tr><th align="left" style="padding:8px">Operational hours</th><td style="padding:8px">{readings['hours']:.1f} h</td></tr>
    </table>
    <h2 style="margin-top:24px;font-size:16px">Recommended next steps</h2>
    <p>Verify the readings, inspect nearby joints and valves, and arrange a field inspection. Do not treat this prediction as a confirmed leak location.</p>
    <p><a href="{safe_dashboard}" style="display:inline-block;padding:11px 16px;border-radius:7px;background:#087a70;color:#fff;text-decoration:none">Open HydroWatch dashboard</a></p>
  </main>
</body></html>"""
    return text_body, html_body


def send_alert(
    alert_id: int,
    user_name: str,
    analysis_id: int,
    created_at: str,
    score: float | None,
    readings: dict[str, float],
    source_mode: str,
    recipient: str,
) -> tuple[str, str]:
    text_body, html_body = alert_content(
        user_name, alert_id, analysis_id, created_at, score, readings, source_mode
    )
    status, message = deliver_email(
        recipient,
        f"HydroWatch alert #{alert_id}: leak suspected",
        text_body,
        html_body,
    )
    with db() as connection:
        connection.execute(
            """UPDATE alerts SET status = ?, status_message = ?, attempts = attempts + 1,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (status, message, alert_id),
        )
    return status, message


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if not valid_csrf():
            return render_template("register.html", error="Form expired. Please try again."), 400
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(name) < 2 or len(name) > 100:
            error = "Name must be between 2 and 100 characters."
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must contain at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            try:
                with db() as connection:
                    connection.execute(
                        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                        (name, email, generate_password_hash(password)),
                    )
                return redirect(url_for("login", registered="1"))
            except sqlite3.IntegrityError:
                error = "An account with this email already exists."
        return render_template("register.html", error=error, name=name, email=email), 400
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not valid_csrf():
            return render_template("login.html", error="Form expired. Please try again."), 400
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with db() as connection:
            user = connection.execute(
                "SELECT id, name, email, password_hash FROM users WHERE email = ?", (email,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["_csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("home"))
        return render_template("login.html", error="Invalid email or password.", email=email), 401
    return render_template("login.html", registered=request.args.get("registered") == "1")


@app.route("/logout", methods=["POST"])
@login_required
@csrf_protected
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    user = current_user()
    with db() as connection:
        counts = connection.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(CASE WHEN prediction = 1 THEN 1 ELSE 0 END), 0) AS leaks,
                      COALESCE(SUM(CASE WHEN prediction = 0 THEN 1 ELSE 0 END), 0) AS normal
               FROM analyses WHERE user_id = ?""", (user["id"],)
        ).fetchone()
        recent = connection.execute(
            "SELECT * FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 8",
            (user["id"],),
        ).fetchall()
        latest_alert = connection.execute(
            """SELECT a.*, x.created_at AS analysis_time
               FROM alerts a JOIN analyses x ON x.id = a.analysis_id
               WHERE a.user_id = ? ORDER BY a.id DESC LIMIT 1""",
            (user["id"],),
        ).fetchone()
        network_count = connection.execute(
            "SELECT COUNT(*) FROM pipeline_networks WHERE user_id = ?", (user["id"],)
        ).fetchone()[0]
        latest_civil = connection.execute(
            "SELECT id, created_at FROM civil_assessments WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
    return render_template(
        "index.html", user=user, counts=counts, recent=recent,
        latest_alert=latest_alert, network_count=network_count,
        latest_civil=latest_civil, model_available=model is not None,
        model_error=model_error, mail_ready=mail_configuration() is not None,
        mail_error=mail_configuration_error(),
    )


@app.route("/predict", methods=["POST"])
@login_required
@csrf_protected
def predict():
    data = request.get_json(silent=True)
    try:
        require_model()
        readings = validate_readings(data)
        source = data.get("source_mode", "manual")
        if source not in ("manual", "demo"):
            raise ValueError("Source mode must be manual or demo.")
        input_frame = pd.DataFrame([[
            readings["pressure"], readings["flow"], readings["temperature"],
            readings["vibration"], readings["rpm"], readings["hours"],
        ]], columns=FEATURES)
        prediction = int(model.predict(input_frame)[0])
        class_labels = list(model.classes_)
        score = None
        if hasattr(model, "predict_proba") and 1 in class_labels:
            values = model.predict_proba(input_frame)[0]
            score = float(values[class_labels.index(1)] * 100)

        user = current_user()
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,100}", key):
            key = str(uuid.uuid4())
        with db() as connection:
            existing = connection.execute(
                "SELECT * FROM analyses WHERE user_id = ? AND idempotency_key = ?",
                (user["id"], key),
            ).fetchone()
            if existing:
                alert = connection.execute(
                    "SELECT status, status_message, recipient, id FROM alerts WHERE analysis_id = ?",
                    (existing["id"],),
                ).fetchone()
                return jsonify(
                    analysis_id=existing["id"], prediction="Leak suspected" if existing["prediction"] else "Normal",
                    status="danger" if existing["prediction"] else "safe",
                    probability=existing["leak_score"], readings=json.loads(existing["readings_json"]),
                    source_mode=existing["source_mode"], timestamp=existing["created_at"],
                    alert=dict(alert) if alert else None, duplicate=True,
                )
            cursor = connection.execute(
                """INSERT INTO analyses
                   (user_id, idempotency_key, source_mode, prediction, leak_score, readings_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["id"], key, source, prediction, score, json.dumps(readings)),
            )
            analysis_id = cursor.lastrowid
            analysis = connection.execute(
                "SELECT created_at FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
            alert_id = None
            if prediction == 1:
                cursor = connection.execute(
                    """INSERT INTO alerts
                       (user_id, analysis_id, recipient, status, status_message)
                       VALUES (?, ?, ?, 'pending', 'Delivery pending.')""",
                    (user["id"], analysis_id, user["email"]),
                )
                alert_id = cursor.lastrowid
        alert_result = None
        if alert_id is not None:
            status, message = send_alert(
                alert_id, user["name"], analysis_id, analysis["created_at"],
                score, readings, source, user["email"],
            )
            alert_result = {
                "id": alert_id, "status": status, "status_message": message,
                "recipient": user["email"],
            }
        return jsonify(
            analysis_id=analysis_id,
            prediction="Leak suspected" if prediction else "Normal",
            status="danger" if prediction else "safe",
            probability=round(score, 2) if score is not None else None,
            probability_note="Uncalibrated model score; not a real-world leak probability.",
            readings=readings, source_mode=source, timestamp=analysis["created_at"],
            alert=alert_result,
        )
    except (ValueError, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    except (KeyError, TypeError) as error:
        app.logger.exception("Prediction request could not be processed")
        return jsonify(error="Prediction request could not be processed."), 400


@app.route("/api/dashboard")
@login_required
def dashboard_data():
    user_id = current_user()["id"]
    with db() as connection:
        records = connection.execute(
            """SELECT id, source_mode, prediction, leak_score, readings_json, created_at
               FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 8""", (user_id,)
        ).fetchall()
        count = connection.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN prediction = 1 THEN 1 ELSE 0 END) leaks,
                      SUM(CASE WHEN prediction = 0 THEN 1 ELSE 0 END) normal
               FROM analyses WHERE user_id = ?""", (user_id,)
        ).fetchone()
    return jsonify(
        totals={"analyses": count["total"], "leaks": count["leaks"] or 0, "normal": count["normal"] or 0},
        recent=[{
            "id": row["id"], "source_mode": row["source_mode"],
            "prediction": "Leak suspected" if row["prediction"] else "Normal",
            "probability": row["leak_score"], "readings": json.loads(row["readings_json"]),
            "created_at": row["created_at"],
        } for row in records],
    )


@app.route("/history")
@login_required
def history():
    with db() as connection:
        records = connection.execute(
            """SELECT id, source_mode, prediction, leak_score, readings_json, created_at
               FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 200""",
            (current_user()["id"],),
        ).fetchall()
    return render_template("history.html", records=records, user=current_user())


@app.route("/alerts")
@login_required
def alerts():
    with db() as connection:
        records = connection.execute(
            """SELECT a.*, x.created_at AS analysis_time FROM alerts a
               JOIN analyses x ON x.id = a.analysis_id
               WHERE a.user_id = ? ORDER BY a.id DESC LIMIT 200""",
            (current_user()["id"],),
        ).fetchall()
    return render_template(
        "alerts.html", alerts=records, user=current_user(),
        config_ready=mail_configuration() is not None,
        config_error=mail_configuration_error(),
    )


@app.route("/alerts/test", methods=["POST"])
@login_required
@csrf_protected
def test_email():
    user = current_user()
    now = datetime.now(timezone.utc).timestamp()
    previous = session.get("last_test_email_at", 0)
    if now - previous < 60:
        flash("Please wait one minute before sending another test email.", "error")
        return redirect(url_for("alerts"))
    session["last_test_email_at"] = now
    status, message = deliver_email(
        user["email"], "HydroWatch test email",
        f"Hello {user['name']},\n\nThis test confirms the HydroWatch email configuration "
        "can submit a message to your registered account address.\n",
    )
    flash(message, "success" if status == "sent" else "error")
    return redirect(url_for("alerts"))


@app.route("/alerts/<int:alert_id>/retry", methods=["POST"])
@login_required
@csrf_protected
def retry_alert(alert_id: int):
    user = current_user()
    with db() as connection:
        row = connection.execute(
            """SELECT a.*, x.created_at AS analysis_time, x.leak_score,
                      x.readings_json, x.source_mode
               FROM alerts a JOIN analyses x ON x.id = a.analysis_id
               WHERE a.id = ? AND a.user_id = ?""", (alert_id, user["id"])
        ).fetchone()
        if row is None:
            return jsonify(error="Alert not found."), 404
        if row["status"] != "failed":
            flash("Only failed alerts can be retried.", "error")
            return redirect(url_for("alerts"))
        if row["recipient"] != user["email"]:
            return jsonify(error="Alert recipient no longer matches the registered account."), 409
    send_alert(
        row["id"], user["name"], row["analysis_id"], row["analysis_time"],
        row["leak_score"], json.loads(row["readings_json"]),
        row["source_mode"], user["email"],
    )
    flash("Alert retry completed. Review the delivery status below.", "success")
    return redirect(url_for("alerts"))


@app.route("/civil", methods=["GET", "POST"])
@login_required
def civil():
    user_id = current_user()["id"]
    if request.method == "POST":
        if not valid_csrf():
            return jsonify(error="Invalid or missing CSRF token."), 400
        network = request.get_json(silent=True)
        try:
            result = assess_network(network)
            serialized = json.dumps(network)
            with db() as connection:
                connection.execute(
                    """INSERT INTO pipeline_networks (user_id, name, configuration_json)
                       VALUES (?, ?, ?)
                       ON CONFLICT(user_id, name) DO UPDATE SET
                         configuration_json = excluded.configuration_json,
                         updated_at = CURRENT_TIMESTAMP""",
                    (user_id, result["name"], serialized),
                )
                network_id = connection.execute(
                    "SELECT id FROM pipeline_networks WHERE user_id = ? AND name = ?",
                    (user_id, result["name"]),
                ).fetchone()["id"]
                cursor = connection.execute(
                    "INSERT INTO civil_assessments (user_id, network_id, result_json) VALUES (?, ?, ?)",
                    (user_id, network_id, json.dumps(result)),
                )
                assessment_id = cursor.lastrowid
            result["assessment_id"] = assessment_id
            return jsonify(result)
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400

    with db() as connection:
        previous = connection.execute(
            """SELECT c.id, c.created_at, n.name FROM civil_assessments c
               JOIN pipeline_networks n ON n.id = c.network_id
               WHERE c.user_id = ? ORDER BY c.id DESC LIMIT 10""", (user_id,)
        ).fetchall()
    return render_template("civil.html", user=current_user(), assessments=previous)


@app.route("/civil/<int:assessment_id>/export.csv")
@login_required
def export_civil_csv(assessment_id: int):
    with db() as connection:
        assessment = connection.execute(
            """SELECT c.id, c.result_json, c.created_at, n.name FROM civil_assessments c
               JOIN pipeline_networks n ON n.id = c.network_id
               WHERE c.id = ? AND c.user_id = ?""",
            (assessment_id, current_user()["id"]),
        ).fetchone()
    if assessment is None:
        return "Assessment not found.", 404
    result = json.loads(assessment["result_json"])
    output = io.StringIO()
    writer = csv.writer(output)
    safe_text = lambda value: (
        "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
        else value
    )
    writer.writerow(["Assessment ID", assessment["id"]])
    writer.writerow(["Network", safe_text(assessment["name"])])
    writer.writerow(["Created at", assessment["created_at"]])
    writer.writerow(["Estimated summed head loss (m)", result["total_head_loss_m"]])
    writer.writerow(["Pressure equivalent (kPa; not junction pressure)", result["pressure_equivalent_kpa"]])
    if result["elevation_change"]:
        writer.writerow(["Elevation change (m)", result["elevation_change"]["difference_m"]])
        writer.writerow(["Elevation pressure-head equivalent (kPa)", result["elevation_change"]["pressure_difference_kpa"]])
    writer.writerow([])
    writer.writerow(["Junction ID", "Name", "Elevation (m)", "Demand (L/s)",
                     "Measured pressure (kPa)", "Flow-balance residual (L/s)"])
    for junction in result["junctions"]:
        writer.writerow([safe_text(junction["id"]), safe_text(junction["name"]), junction["elevation_m"],
                         junction["demand_l_s"], junction["pressure_kpa"],
                         junction["flow_balance_l_s"]])
    writer.writerow([])
    writer.writerow(["Pipe ID", "Material", "Absolute roughness (mm)", "Upstream", "Downstream",
                     "Length (m)", "Diameter (mm)", "Flow (L/s)", "Velocity (m/s)",
                     "Reynolds number", "Friction factor", "Friction head (m)",
                     "Minor head (m)", "Total head loss (m)"])
    for pipe in result["pipes"]:
        writer.writerow([safe_text(pipe["id"]), safe_text(pipe["material"]), pipe["roughness_mm"],
                         safe_text(pipe["upstream"]), safe_text(pipe["downstream"]), pipe["length_m"],
                         pipe["diameter_mm"], pipe["flow_l_s"], pipe["velocity_m_s"],
                         pipe["reynolds"], pipe["friction_factor"], pipe["friction_head_m"],
                         pipe["minor_head_m"], pipe["total_head_loss_m"]])
    writer.writerow([])
    for label, entries in (("Assumption", result["assumptions"]), ("Warning", result["warnings"])):
        for entry in entries:
            writer.writerow([label, safe_text(entry)])
    for priority in result["inspection_priorities"]:
        writer.writerow([
            "Hydraulic review priority",
            safe_text(priority["pipe_id"]),
            safe_text(", ".join(priority["junctions"])),
            safe_text(priority["reason"]),
        ])
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=hydrowatch-assessment-{assessment_id}.csv"},
    )


@app.route("/model")
@login_required
def model_page():
    civil_summary = None
    if CIVIL_DATA_PATH.is_file():
        try:
            frame = pd.read_csv(CIVIL_DATA_PATH)
            civil_summary = {
                "filename": CIVIL_DATA_PATH.name, "rows": int(len(frame)),
                "columns": list(frame.columns),
            }
        except (OSError, pd.errors.ParserError):
            civil_summary = {"filename": CIVIL_DATA_PATH.name, "error": "Could not read the civil dataset."}
    return render_template(
        "model.html", user=current_user(), evaluation=model_evaluation,
        model_error=model_error, civil_summary=civil_summary,
    )


@app.route("/help")
@login_required
def help_page():
    return render_template("help.html", user=current_user())


@app.route("/settings")
@login_required
def settings():
    user = current_user()
    return render_template(
        "info.html", user=user, title="Settings",
        message="Account email alerts are sent only to your registered email address. "
                "SMTP is " + ("configured." if mail_configuration() else "not configured."),
    )


@app.errorhandler(500)
def internal_error(_error):
    app.logger.exception("Unhandled HydroWatch server error")
    return render_template("error.html", message="An unexpected server error occurred."), 500


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
