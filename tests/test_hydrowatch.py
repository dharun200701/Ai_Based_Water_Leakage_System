import os
import sqlite3
import smtplib
import ssl
import tempfile
import unittest
from unittest.mock import patch
from email.message import EmailMessage

import numpy as np
import pandas as pd

import app as application
from civil_engineering import assess_network, friction_factor
from model_service import FEATURES


READINGS = {
    "pressure": 25, "flow": 180, "temperature": 22,
    "vibration": 2.5, "rpm": 1500, "hours": 2500,
    "source_mode": "manual",
}


class LeakModel:
    classes_ = np.array([0, 1])

    def __init__(self, prediction=1):
        self.prediction = prediction
        self.input_columns = None

    def predict(self, frame):
        self.input_columns = list(frame.columns)
        return np.array([self.prediction])

    def predict_proba(self, frame):
        return np.array([[0.15, 0.85]])


class HydroWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = application.DATABASE
        application.DATABASE = os.path.join(self.temp_dir.name, "test.db")
        application.init_db()
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()
        with application.db() as connection:
            connection.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                ("A User", "a@example.test", "hash"),
            )
            connection.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                ("B User", "b@example.test", "hash"),
            )
        self.authenticate(1)

    def tearDown(self):
        application.DATABASE = self.previous_database
        self.temp_dir.cleanup()

    def authenticate(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "test-csrf-token"

    def headers(self, key="request-key-0001"):
        return {"X-CSRF-Token": "test-csrf-token", "Idempotency-Key": key}

    def post_prediction(self, values=None, key="request-key-0001"):
        payload = values or dict(READINGS)
        return self.client.post(
            "/predict", json=payload, headers=self.headers(key),
        )

    def test_registration_login_and_password_hash(self):
        registered = self.client.post(
            "/register",
            data={
                "csrf_token": "test-csrf-token", "name": "New User",
                "email": "new@example.test", "password": "securepass1",
                "confirm_password": "securepass1",
            },
        )
        self.assertEqual(registered.status_code, 302)
        with application.db() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                ("new@example.test",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["password_hash"], "securepass1")
        failed_login = self.client.post(
            "/login", data={"csrf_token": "test-csrf-token", "email": "new@example.test", "password": "wrongpass"}
        )
        self.assertEqual(failed_login.status_code, 401)
        successful_login = self.client.post(
            "/login", data={"csrf_token": "test-csrf-token", "email": "new@example.test", "password": "securepass1"}
        )
        self.assertEqual(successful_login.status_code, 302)

    def test_prediction_validates_feature_order_and_persists(self):
        fake_model = LeakModel()
        with patch.object(application, "model", fake_model), patch(
            "app.deliver_email", return_value=("sent", "accepted")
        ) as send:
            payload = dict(READINGS, recipient="attacker@example.test")
            response = self.post_prediction(payload)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["prediction"], "Leak suspected")
        self.assertEqual(result["probability"], 85.0)
        self.assertEqual(fake_model.input_columns, FEATURES)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "a@example.test")
        self.assertIn("Manual user-entered readings", send.call_args.args[2])
        self.assertIn("not live hardware measurements", send.call_args.args[3])
        with application.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT status FROM alerts").fetchone()[0], "sent")

    def test_idempotent_request_does_not_duplicate_analysis_or_email(self):
        with patch.object(application, "model", LeakModel()), patch(
            "app.deliver_email", return_value=("sent", "accepted")
        ) as send:
            self.post_prediction(key="retry-token-00001")
            response = self.post_prediction(key="retry-token-00001")
        self.assertTrue(response.get_json()["duplicate"])
        send.assert_called_once()
        with application.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 1)

    def test_invalid_manual_reading_is_rejected(self):
        response = self.post_prediction({"pressure": -1})
        self.assertEqual(response.status_code, 400)
        self.assertIn("pressure", response.get_json()["error"])
        with application.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 0)

    def test_missing_csrf_token_rejected(self):
        response = self.client.post("/predict", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSRF", response.get_json()["error"])

    def test_dashboard_uses_only_saved_analysis_statistics(self):
        page = self.client.get("/")
        self.assertIn(b"No analyses yet", page.data)
        with patch.object(application, "model", LeakModel(0)), patch(
            "app.deliver_email"
        ) as send:
            self.post_prediction(key="normal-request-0001")
        send.assert_not_called()
        with application.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 0)
        page = self.client.get("/")
        self.assertIn(b"Analyses performed", page.data)
        self.assertIn(b"Normal", page.data)

    def test_dashboard_marks_old_failure_as_historical_when_smtp_is_now_ready(self):
        with application.db() as connection:
            cursor = connection.execute(
                """INSERT INTO analyses
                   (user_id,idempotency_key,source_mode,prediction,leak_score,readings_json)
                   VALUES (1,'historic-alert-0001','manual',1,80,'{}')"""
            )
            analysis_id = cursor.lastrowid
            cursor = connection.execute(
                """INSERT INTO alerts
                   (user_id,analysis_id,recipient,status,status_message)
                   VALUES (1,?,'a@example.test','failed','Email alerts not configured.')""",
                (analysis_id,),
            )
            alert_id = cursor.lastrowid
        with patch("app.mail_configuration", return_value={"host": "smtp.example.test"}):
            page = self.client.get("/")
        self.assertIn(f"Alert #{alert_id} for analysis #{analysis_id}".encode(), page.data)
        self.assertIn(b"This alert records an earlier failed attempt", page.data)
        self.assertIn(b"has not been resent automatically", page.data)

    def test_user_data_isolation(self):
        with patch.object(application, "model", LeakModel()), patch(
            "app.deliver_email", return_value=("failed", "Email alerts not configured.")
        ):
            self.post_prediction(key="user-a-analysis-01")
        self.authenticate(2)
        self.assertNotIn(b"user-a-analysis-01", self.client.get("/history").data)
        self.assertIn(b"No alerts yet", self.client.get("/alerts").data)

    def test_failed_email_status_persists_and_can_retry(self):
        with patch.object(application, "model", LeakModel()), patch(
            "app.deliver_email", return_value=("failed", "Email delivery failed.")
        ):
            response = self.post_prediction(key="failed-email-0001")
        self.assertEqual(response.get_json()["prediction"], "Leak suspected")
        self.assertEqual(response.get_json()["alert"]["status"], "failed")
        self.assertIn("failed", response.get_json()["alert"]["status_message"].lower())
        with application.db() as connection:
            alert_id = connection.execute("SELECT id FROM alerts").fetchone()["id"]
        with patch("app.deliver_email", return_value=("sent", "accepted")) as deliver:
            response = self.client.post(
                f"/alerts/{alert_id}/retry",
                data={"csrf_token": "test-csrf-token"},
            )
        self.assertEqual(response.status_code, 302)
        deliver.assert_called_once()
        with application.db() as connection:
            status = connection.execute("SELECT status FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]
        self.assertEqual(status, "sent")

    def test_smtp_ssl_sends_multipart_email_with_default_context(self):
        smtp = unittest.mock.MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}
        config = {
            "host": "smtp.example.test", "port": 465,
            "username": "sender@example.test", "password": "app-password",
            "sender": "sender@example.test", "ssl": True, "tls": False,
        }
        with patch("app.mail_configuration", return_value=config), patch(
            "app.smtplib.SMTP_SSL", return_value=smtp
        ) as smtp_ssl:
            status, message = application.deliver_email(
                "recipient@example.test", "HydroWatch alert", "plain body",
                "<html><body>html body</body></html>",
            )
        self.assertEqual(status, "sent")
        self.assertIn("accepted", message)
        args, kwargs = smtp_ssl.call_args
        self.assertEqual(args[:2], ("smtp.example.test", 465))
        self.assertIsInstance(kwargs["context"], ssl.SSLContext)
        smtp.starttls.assert_not_called()
        sent = smtp.send_message.call_args.args[0]
        self.assertIsInstance(sent, EmailMessage)
        self.assertEqual(sent.get_content_type(), "multipart/alternative")
        self.assertEqual(sent.get_body(preferencelist=("plain",)).get_content().strip(), "plain body")
        self.assertIn("html body", sent.get_body(preferencelist=("html",)).get_content())
        self.assertEqual(str(sent["To"]), "recipient@example.test")

    def test_missing_smtp_configuration_names_missing_values(self):
        with patch.dict(os.environ, {
            "MAIL_SERVER": "smtp.example.test",
            "MAIL_PORT": "465",
            "MAIL_USE_SSL": "true",
            "MAIL_USE_TLS": "false",
            "MAIL_USERNAME": "sender@example.test",
            "MAIL_PASSWORD": "",
            "MAIL_DEFAULT_SENDER": "sender@example.test",
        }, clear=True):
            config, error = application.read_mail_configuration()
        self.assertIsNone(config)
        self.assertEqual(error, "Missing or empty SMTP setting(s): MAIL_PASSWORD.")

    def test_mail_configuration_parses_true_and_false_case_insensitively(self):
        with patch.dict(os.environ, {
            "MAIL_SERVER": "smtp.gmail.com",
            "MAIL_PORT": "465",
            "MAIL_USE_SSL": "TrUe",
            "MAIL_USE_TLS": "FALSE",
            "MAIL_USERNAME": "sender@example.test",
            "MAIL_PASSWORD": "abcd efgh",
            "MAIL_DEFAULT_SENDER": "sender@example.test",
        }, clear=True):
            config, error = application.read_mail_configuration()
        self.assertIsNone(error)
        self.assertEqual(config["port"], 465)
        self.assertTrue(config["ssl"])
        self.assertFalse(config["tls"])
        self.assertEqual(config["password"], "abcdefgh")

    def test_starttls_used_when_ssl_is_disabled(self):
        smtp = unittest.mock.MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}
        config = {
            "host": "smtp.example.test", "port": 587,
            "username": "sender@example.test", "password": "app-password",
            "sender": "sender@example.test", "ssl": False, "tls": True,
        }
        with patch("app.mail_configuration", return_value=config), patch(
            "app.smtplib.SMTP", return_value=smtp
        ) as smtp_plain:
            status, _ = application.deliver_email(
                "recipient@example.test", "Subject", "plain body"
            )
        self.assertEqual(status, "sent")
        self.assertIsInstance(smtp.starttls.call_args.kwargs["context"], ssl.SSLContext)
        smtp_plain.assert_called_once()

    def test_smtp_failure_is_logged_without_blocking_leak_prediction(self):
        smtp = unittest.mock.MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.side_effect = smtplib.SMTPException("delivery refused")
        config = {
            "host": "smtp.example.test", "port": 465,
            "username": "sender@example.test", "password": "private-password",
            "sender": "sender@example.test", "ssl": True, "tls": False,
        }
        with patch("app.mail_configuration", return_value=config), patch(
            "app.smtplib.SMTP_SSL", return_value=smtp
        ), self.assertLogs("app", level="WARNING") as logs:
            status, message = application.deliver_email(
                "recipient@example.test", "Subject", "plain body"
            )
        self.assertEqual(status, "failed")
        self.assertIn("delivery failed", message.lower())
        self.assertNotIn("private-password", "\n".join(logs.output))

    def test_alert_content_distinguishes_simulated_and_manual_inputs(self):
        readings = {key: value for key, value in READINGS.items() if key != "source_mode"}
        manual, manual_html = application.alert_content(
            "Test User", 7, 11, "2026-10-29 12:00:00", 83.5, readings, "manual"
        )
        simulated, simulated_html = application.alert_content(
            "Test User", 8, 12, "2026-10-29 12:01:00", None, readings, "demo"
        )
        self.assertIn("Manual user-entered readings", manual)
        self.assertIn("Simulated demo readings", simulated)
        self.assertIn("not live hardware measurements", manual_html)
        self.assertIn("Simulated demo readings", simulated_html)
        self.assertIn("83.50%", manual_html)
        self.assertNotIn("Model score", simulated)

    def test_civil_assessment_persists_and_exports_csv(self):
        network = {
            "name": "Test branch",
            "junctions": [
                {"id": "J1", "elevation_m": 2, "demand_l_s": 0},
                {"id": "J2", "elevation_m": 1, "demand_l_s": 0},
            ],
            "pipes": [{
                "id": "P1", "upstream": "J1", "downstream": "J2",
                "length_m": 100, "diameter_mm": 100, "flow_l_s": 1,
                "material": "pvc", "minor_k": 1,
            }],
            "pump": {},
        }
        response = self.client.post(
            "/civil", json=network, headers={"X-CSRF-Token": "test-csrf-token"}
        )
        self.assertEqual(response.status_code, 200)
        assessment_id = response.get_json()["assessment_id"]
        self.assertGreater(response.get_json()["pipes"][0]["reynolds"], 0)
        export = self.client.get(f"/civil/{assessment_id}/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"Friction head", export.data)

    def test_civil_validation_warns_about_disconnected_junction(self):
        result = assess_network({
            "name": "Disconnected",
            "junctions": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
            "pipes": [{
                "id": "P", "upstream": "A", "downstream": "B",
                "length_m": 10, "diameter_mm": 50, "flow_l_s": 0,
            }],
        })
        self.assertTrue(any("Disconnected junctions: C" in warning for warning in result["warnings"]))
        self.assertEqual(friction_factor(1000, 0.01), 64 / 1000)

    def test_pump_power_and_affinity_estimates(self):
        result = assess_network({
            "name": "Pump check",
            "junctions": [{"id": "A"}, {"id": "B"}],
            "pipes": [{
                "id": "P", "upstream": "A", "downstream": "B",
                "length_m": 20, "diameter_mm": 80, "flow_l_s": 1,
            }],
            "pump": {
                "junction_id": "A", "rpm": 1500, "reference_rpm": 1200,
                "head_m": 10, "flow_l_s": 2, "efficiency": 0.8,
            },
        })
        self.assertAlmostEqual(result["pump"]["hydraulic_power_w"], 195.78, delta=0.1)
        self.assertAlmostEqual(result["pump"]["affinity_estimates"]["head_ratio"], 1.5625)
        self.assertGreater(result["pump"]["estimated_input_power_w"], result["pump"]["hydraulic_power_w"])

    def test_demo_examples_match_the_trained_model_output(self):
        examples = ([60, 80, 25, 3, 2000, 5480], [18, 245, 24, 4.8, 1700, 3400])
        predictions = [
            int(application.model.predict(
                pd.DataFrame([values], columns=FEATURES)
            )[0])
            for values in examples
        ]
        self.assertEqual(predictions, [0, 1])

    def test_private_pages_render(self):
        for path in ("/", "/history", "/alerts", "/civil", "/model", "/help", "/settings"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_protected_route_redirects_without_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

    def test_safe_schema_initialization_preserves_legacy_users(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """CREATE TABLE users (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                   email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Legacy", "legacy@example.test", "existing-hash"),
        )
        connection.commit()
        connection.close()
        old_path = application.DATABASE
        application.DATABASE = legacy_path
        try:
            application.init_db()
            with application.db() as migrated:
                self.assertEqual(migrated.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
                self.assertIsNotNone(migrated.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='analyses'"
                ).fetchone())
        finally:
            application.DATABASE = old_path


if __name__ == "__main__":
    unittest.main()
