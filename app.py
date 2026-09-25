from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import re
import smtplib
import sqlite3
from email.message import EmailMessage
from functools import wraps

import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split


app = Flask(__name__)

# Change this in production
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "hydrowatch-demo-secret-change-me"
)

DATABASE = "hydrowatch.db"

# ============================================================
# DATASET
# ============================================================

BASE_DATA_PATH = "location_aware_gis_leakage_dataset_with_units.csv"
CIVIL_DATA_PATH = "civil_data.csv"

features = [
    "Pressure_PSI",
    "Flow_Rate_Lmin",
    "Temperature_C",
    "Vibration_mms",
    "RPM",
    "Operational_Hours_h"
]

target = "Leakage_Flag"


df = pd.read_csv(BASE_DATA_PATH)
civil_df = pd.read_csv(CIVIL_DATA_PATH)


# ============================================================
# TRAIN MACHINE LEARNING MODEL
# ============================================================

X_train, X_test, y_train, y_test = train_test_split(
    df[features],
    df[target],
    test_size=0.2,
    random_state=42,
    stratify=df[target]
)

model = RandomForestClassifier(
    n_estimators=100,
    random_state=42
)

model.fit(X_train, y_train)

print("Random Forest model trained successfully!")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# LOGIN REQUIRED DECORATOR
# ============================================================

def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if "user_id" not in session:

            if request.path.startswith("/api/") or request.path == "/predict":

                return jsonify({
                    "error": "Login required",
                    "login_required": True
                }), 401

            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


# ============================================================
# CURRENT USER
# ============================================================

def current_user():

    if "user_id" not in session:
        return None

    conn = db()

    user = conn.execute(
        """
        SELECT id, name, email
        FROM users
        WHERE id = ?
        """,
        (session["user_id"],)
    ).fetchone()

    conn.close()

    return user


# ============================================================
# EMAIL FUNCTION
# ============================================================

def send_email_notification(to_email, subject, body):

    # SMTP configuration
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")

    smtp_from = os.getenv(
        "SMTP_FROM",
        smtp_username or ""
    )

    # Check configuration
    if not all([
        smtp_host,
        smtp_username,
        smtp_password,
        smtp_from
    ]):

        return (
            False,
            "SMTP is not configured."
        )

    if not to_email:

        return (
            False,
            "No recipient email found."
        )

    try:

        # Create email
        message = EmailMessage()

        message["Subject"] = subject
        message["From"] = smtp_from
        message["To"] = to_email

        message.set_content(body)

        # Connect to SMTP server
        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=15
        ) as smtp:

            smtp.starttls()

            smtp.login(
                smtp_username,
                smtp_password
            )

            smtp.send_message(message)

        return (
            True,
            f"Email alert sent to {to_email}"
        )

    except Exception as error:

        return (
            False,
            f"Email could not be sent: {error}"
        )


# ============================================================
# REGISTER
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        # Validate name
        if len(name) < 2:

            return render_template(
                "register.html",
                error="Please enter your full name.",
                name=name,
                email=email
            )

        # Validate email
        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        ):

            return render_template(
                "register.html",
                error="Please enter a valid email address.",
                name=name,
                email=email
            )

        # Validate password
        if len(password) < 6:

            return render_template(
                "register.html",
                error="Password must contain at least 6 characters.",
                name=name,
                email=email
            )

        # Confirm password
        if password != confirm_password:

            return render_template(
                "register.html",
                error="Passwords do not match.",
                name=name,
                email=email
            )

        conn = db()

        try:

            password_hash = generate_password_hash(
                password
            )

            conn.execute(
                """
                INSERT INTO users
                (name, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (
                    name,
                    email,
                    password_hash
                )
            )

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return render_template(
                "register.html",
                error="An account with this email already exists.",
                name=name,
                email=email
            )

        conn.close()

        return redirect(
            url_for(
                "login",
                registered="1"
            )
        )

    return render_template(
        "register.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        conn.close()

        # Check login
        if user and check_password_hash(
            user["password_hash"],
            password
        ):

            session.clear()

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            return redirect(
                url_for("home")
            )

        return render_template(
            "login.html",
            error="Invalid email or password.",
            email=email
        )

    registered = (
        request.args.get("registered") == "1"
    )

    return render_template(
        "login.html",
        registered=registered
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
@login_required
def home():

    user = current_user()

    return render_template(
        "index.html",
        user_name=user["name"],
        user_email=user["email"]
    )


# ============================================================
# CIVIL DATA API
# ============================================================

@app.route("/api/civil-data")
@login_required
def civil_data():

    leak_count = int(
        civil_df["Leakage_Flag"].sum()
    )

    zone_summary = (
        civil_df
        .groupby("Zone")
        .agg(
            records=("Zone", "size"),
            leaks=("Leakage_Flag", "sum")
        )
        .reset_index()
    )

    zone_summary["leak_rate"] = (
        zone_summary["leaks"]
        /
        zone_summary["records"]
        *
        100
    ).round(1)

    display_columns = [
        "Scenario_ID",
        "Zone",
        "Block",
        "Pipe",
        "Pressure_PSI",
        "Flow_Rate_Lmin",
        "Vibration_mms",
        "Leakage_Flag",
        "Latitude",
        "Longitude"
    ]

    records = civil_df[
        display_columns
    ]

    records = records[
        records["Leakage_Flag"] == 1
    ].head(6).fillna("")

    records = records.rename(
        columns={
            "Scenario_ID": "Location_Code"
        }
    )

    return jsonify({

        "total_records": len(civil_df),

        "leaks": leak_count,

        "normal": len(civil_df) - leak_count,

        "leak_rate": round(
            leak_count /
            len(civil_df) *
            100,
            1
        ),

        "avg_pressure": round(
            float(
                civil_df["Pressure_PSI"].mean()
            ),
            1
        ),

        "zones": zone_summary.to_dict(
            orient="records"
        ),

        "records": records.to_dict(
            orient="records"
        )

    })


# ============================================================
# LEAK PREDICTION
# ============================================================

@app.route(
    "/predict",
    methods=["POST"]
)
@login_required
def predict():

    data = request.get_json()

    try:

        if not isinstance(data, dict):

            raise ValueError(
                "Request body must be a JSON object"
            )

        # Valid input ranges
        limits = {

            "pressure": (0, 200),

            "flow": (0, 500),

            "temperature": (-50, 150),

            "vibration": (0, 20),

            "rpm": (0, 5000),

            "hours": (0, 100000)
        }

        # Validate inputs
        for field, (
            minimum,
            maximum
        ) in limits.items():

            value = data.get(field)

            if (
                isinstance(value, bool)
                or
                not isinstance(
                    value,
                    (int, float)
                )
            ):

                raise ValueError(
                    f"{field} must be a number"
                )

            if not pd.notna(value):

                raise ValueError(
                    f"{field} must be a finite number"
                )

            if not (
                minimum <= value <= maximum
            ):

                raise ValueError(
                    f"{field} must be between "
                    f"{minimum} and {maximum}"
                )

        # Prepare ML input
        input_data = pd.DataFrame(
            [[

                data["pressure"],

                data["flow"],

                data["temperature"],

                data["vibration"],

                data["rpm"],

                data["hours"]

            ]],
            columns=features
        )

        # Prediction
        prediction = model.predict(
            input_data
        )[0]

        probability = model.predict_proba(
            input_data
        )[0]

        classes = list(
            model.classes_
        )

        if 1 in classes:

            leak_probability = (
                probability[
                    classes.index(1)
                ]
                *
                100
            )

        else:

            leak_probability = 0

        # Current logged-in user
        user = current_user()

        # ====================================================
        # LEAK DETECTED
        # ====================================================

        if prediction == 1:

            result = "Leak Detected"

            status = "danger"

            notification = (
                "⚠️ WATER LEAKAGE ALERT: "
                "A possible water leakage "
                "has been detected. "
                "Please inspect the "
                "pipeline immediately."
            )

            subject = (
                "HydroWatch - "
                "Water Leakage Alert"
            )

            email_body = f"""
WATER LEAKAGE ALERT

Hello {user["name"]},

HydroWatch detected a possible
water leakage in the water network.

Leak probability:
{leak_probability:.2f}%

Sensor information:

Pressure:
{data["pressure"]:.2f} PSI

Flow Rate:
{data["flow"]:.2f} L/min

Temperature:
{data["temperature"]:.2f} °C

Vibration:
{data["vibration"]:.3f} mm/s

RPM:
{data["rpm"]:.1f}

Operating Hours:
{data["hours"]:.1f}

Please inspect the pipeline
immediately.

This is an automated alert
from the HydroWatch
Water Leakage Detection System.
"""

            # SEND EMAIL
            email_sent, email_status = (
                send_email_notification(
                    user["email"],
                    subject,
                    email_body
                )
            )

        # ====================================================
        # NO LEAK
        # ====================================================

        else:

            result = "No Leak"

            status = "safe"

            notification = (
                "✅ No leakage detected. "
                "The water system is "
                "operating normally."
            )

            email_sent = False

            email_status = (
                "No email sent because "
                "no leakage was detected."
            )

        # Return response
        return jsonify({

            "prediction": result,

            "probability": round(
                leak_probability,
                2
            ),

            "status": status,

            "notification": notification,

            "email_sent": email_sent,

            "email_status": email_status,

            "recipient": user["email"]

        })

    except Exception as error:

        return jsonify({

            "error": str(error)

        }), 400


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )