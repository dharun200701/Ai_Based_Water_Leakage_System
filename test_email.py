
import os
import ssl
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv


def main():
    load_dotenv()

    host = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    port = int(os.getenv("MAIL_PORT", "465"))
    username = os.getenv("MAIL_USERNAME")
    password = os.getenv("MAIL_PASSWORD", "").replace(" ", "")
    sender = os.getenv("MAIL_DEFAULT_SENDER") or username

    if not username or not password:
        raise SystemExit("Missing email credentials in .env")

    message = EmailMessage()
    message["Subject"] = "HydroWatch Email Test"
    message["From"] = sender
    message["To"] = username
    message.set_content(
        "HydroWatch email test successful. "
        "This is a test, not a real leak alert."
    )

    try:
        print(f"Connecting to {host}:{port}...")
        with smtplib.SMTP_SSL(
            host,
            port,
            timeout=30,
            context=ssl.create_default_context()
        ) as smtp:
            print("Connected using SSL.")
            smtp.login(username, password)
            print("Login successful.")
            smtp.send_message(message)

        print("Email accepted by SMTP server. Check inbox and spam.")

    except (OSError, smtplib.SMTPException) as error:
        print(f"FAILED: {type(error).__name__}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
