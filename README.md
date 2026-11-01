# HydroWatch

HydroWatch is a local Flask demonstration application for AI-based water leakage prediction and preliminary civil pipeline assessment. It runs without physical sensors. User-entered, simulated, and CSV training values are kept distinct.

## What is implemented

- Flask registration, sign-in, sign-out, Werkzeug password hashes, session authentication, CSRF checks, and owner-scoped records.
- Reproducible Random Forest training/evaluation on the six project features. Dataset validation and actual holdout metrics are shown in the Dataset & Model page.
- Manual analysis and clearly labeled simulated normal/suspected-leak scenarios. The application stores each analysis and its readings.
- User-specific email alert records and delivery state (`pending`, `sent`, `failed`), SMTP delivery via Python's standard email library, a test email limited to the authenticated user's registered address, and retry for failed alerts.
- Junction/pipe configuration, duplicate and connection validation, disconnected-network and flow-balance warnings, pipe velocity, Reynolds number, friction/minor head loss, pressure-equivalent estimate, optional pump-power and same-pump affinity-law estimates, and CSV report export.
- Safe additive SQLite schema initialization; existing `users` rows are preserved.

The civil calculation is a preliminary estimate, not a full hydraulic solver. Supplied flows are not solved from a network boundary condition. Model classifier scores are uncalibrated and are not real-world leak probabilities. No junction is reported as a confirmed leak location.

## Windows / VS Code setup

Open PowerShell in the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a random `FLASK_SECRET_KEY`. SMTP settings are optional. If SMTP is configured, use a legitimate SMTP provider's host, port, TLS settings, account username, app-specific password, and sender address. Do not commit `.env` or share SMTP credentials. `MAIL_DEFAULT_SENDER` should be an address accepted by the configured provider. `APP_BASE_URL` is used in alert email text.

For the Gmail SSL setup, the relevant `.env` entries should be shaped like this (put your own account and app password in `.env`, not in source control):

```dotenv
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=465
MAIL_USE_TLS=false
MAIL_USE_SSL=true
MAIL_USERNAME=your-account@gmail.com
MAIL_PASSWORD=your-google-app-password
MAIL_DEFAULT_SENDER=your-account@gmail.com
```

The application loads `.env` from the project directory using `python-dotenv`. SSL mode uses `SMTP_SSL` with the system's default TLS certificate context. If SSL is disabled and `MAIL_USE_TLS=true`, the application negotiates STARTTLS. A leak alert is created only after the actual model predicts a leak; the recipient comes from the signed-in session's user record in SQLite, never from request data. The message includes both plain-text and HTML alternatives and labels manual/demo readings as non-live. A failed delivery leaves the prediction intact and records a failed alert for retry.

After changing `.env`, restart Flask so the process reloads the settings. Existing `failed` alert rows are historical and are not changed to `sent` just because the current SMTP configuration is valid. Open **Email Alerts** and explicitly retry a failed alert; this avoids automatically duplicating a legitimate prior leak event. The dashboard identifies old “Email alerts not configured” failures when SMTP is now ready and links to the retry page. SMTP authentication failures show a safe corrective message without including the SMTP response or password.

The app loads `.env` on startup, creates missing tables, and preserves the existing `hydrowatch.db` and user data. Start it with:

```powershell
python app.py
```

Open <http://127.0.0.1:5000>. Debug mode is off by default. To run tests (SMTP is mocked and the suite does not send mail):

```powershell
python -m unittest discover -v
```

## Demonstration without hardware

1. Register and sign in.
2. On the dashboard choose **Load normal demo**, then **Run analysis**. The readings are stored with source `demo`.
3. Choose **Load suspected-leak demo** and run it. The model's actual result determines whether an email-alert record is created; inspect its current status under **Email Alerts**.
4. Configure Gmail SSL in `.env`, restart the app, and ensure the signed-in account has the intended recipient address registered in the existing `users` table. Run a demo or manual analysis that the model classifies as a leak, then verify the alert's sent/failed status under **Email Alerts** and check the registered inbox/spam folder. The **Send test email** button also sends only to that signed-in account's registered email.
5. Open **Civil Pipeline Assessment**, load the illustrative example, calculate the estimate, review warnings and per-pipe results, then download the CSV.
6. Open **Dataset & Model** to inspect actual dataset rows, missing-value summary, class distribution, split size, and holdout metrics.

The demo input values are illustrative only; they are not measured sensor readings or experiment evidence. A given scenario name does not override the classifier output.

## Database and data

The database remains `hydrowatch.db`. Inspect it with the VS Code SQLite extension or another SQLite browser. Main tables are `users`, `analyses`, `alerts`, `pipeline_networks`, and `civil_assessments`. User-owned analysis, alert, and civil records are always queried by the authenticated user ID. SQLite foreign-key checks are enabled per connection.

`location_aware_gis_leakage_dataset_with_units.csv` supplies model training/evaluation rows. `civil_data.csv` is inspected by the evaluation page for its actual row count and column names; it is not shown as live telemetry. The app does not import either CSV into a live-analysis history.

## Engineering calculation notes

- Internal calculation units are SI: metres, m³/s, Pa-equivalent, kg/m³, and Pa·s. User flows are entered in L/s, diameters/roughness in mm, and elevations/lengths in m.
- Darcy–Weisbach friction loss is used with `64/Re` for laminar flow, a Swamee–Jain turbulent estimate, and linear interpolation through the transition region. Optional minor loss uses `K v²/(2g)`.
- Water density and dynamic viscosity are approximated at 20 °C. Material roughness suggestions are common estimates and can be overridden.
- Hydraulic power is computed only when pump flow and head are both supplied. Efficiency is an optional fraction from 0 to 1.
- Affinity-law ratios are offered only when both current and reference RPM are supplied, and are explicitly estimates for the same pump under suitable operating assumptions.
- Summed pipe head loss converted to kPa is labeled an equivalent only; it is not a predicted pressure at a junction.

## Current limitations / future work

- No physical sensors, microcontroller, IoT gateway, GIS live feed, or pump connection is present.
- No general-purpose nonlinear network hydraulic solver, calibrated leak probability, real field validation, or confirmed leak localization is included.
- SMTP delivery requires valid external provider credentials and connectivity. Automated tests mock delivery and never send email.
- `python test_email.py` is an explicit standalone SMTP connectivity test that sends a test message to `MAIL_USERNAME`; it is not run by the automated test suite.
- PDF reports, arbitrary valve/tank boundary modelling, configurable water-temperature properties, and production deployment hardening remain future work.
