#!/usr/bin/env python3
"""Send queued activation-code notices through SMTP."""

import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

import mysql.connector
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("mysql-email-notifier")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


DB_CONFIG = {
    "host": required("DB_HOST"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": required("DB_USER"),
    "password": required("DB_PASSWORD"),
    "database": "db_gigel_prod",
    "connection_timeout": 10,
    "autocommit": False,
}
SMTP_HOST = required("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = required("SMTP_USER")
SMTP_PASSWORD = required("SMTP_PASSWORD")
FROM_EMAIL = required("FROM_EMAIL")
FROM_NAME = os.getenv("FROM_NAME", "Admin Notification")
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "5")))
MAX_ATTEMPTS = max(1, int(os.getenv("MAX_ATTEMPTS", "8")))


def claim_one():
    """Claim one ready job atomically and recover stale worker locks."""
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "UPDATE email_notification_queue SET status='pending', locked_at=NULL "
            "WHERE status='processing' AND locked_at < (UTC_TIMESTAMP() - INTERVAL 10 MINUTE)"
        )
        cur.execute(
            "SELECT id, recipient_email, activation_code, source_user_id, event_type, attempts "
            "FROM email_notification_queue WHERE status='pending' "
            "AND next_attempt_at <= UTC_TIMESTAMP() ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
        )
        row = cur.fetchone()
        if row:
            row["attempts"] += 1
            cur.execute(
                "UPDATE email_notification_queue SET status='processing', "
                "locked_at=UTC_TIMESTAMP(), attempts=attempts+1 WHERE id=%s",
                (row["id"],),
            )
        conn.commit()
        cur.close()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def send_email(job):
    event_label = "kode aktivasi baru" if job["event_type"] == "insert" else "perubahan kode aktivasi"
    msg = EmailMessage()
    msg["Subject"] = f"Notifikasi {event_label} akun Anda"
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = job["recipient_email"]
    msg.set_content(
        "Halo,\n\n"
        f"Kami mencatat {event_label} untuk akun Anda.\n"
        f"Kode aktivasi: {job['activation_code']}\n\n"
        "Jika Anda tidak meminta perubahan ini, abaikan email ini dan hubungi administrator.\n"
    )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def update_job(job_id, sql, params):
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(sql, (*params, job_id))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def mark_failed(job, error):
    attempts = job["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        status, delay = "failed", 0
    else:
        status, delay = "pending", min(3600, 30 * (2 ** min(attempts - 1, 7)))
    # Error text is operational metadata. Never log the email body or activation code.
    # Avoid storing provider exception strings, which can contain message/address data.
    error_text = type(error).__name__[:500]
    update_job(
        job["id"],
        "UPDATE email_notification_queue SET status=%s, locked_at=NULL, last_error=%s, "
        "next_attempt_at=UTC_TIMESTAMP() + INTERVAL %s SECOND WHERE id=%s",
        (status, error_text, delay),
    )
    log.error("Email job %s failed (attempt %s/%s): %s", job["id"], attempts, MAX_ATTEMPTS, error)


def main():
    log.info("Worker started")
    while True:
        try:
            job = claim_one()
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            try:
                send_email(job)
                update_job(
                    job["id"],
                    "UPDATE email_notification_queue SET status='sent', sent_at=UTC_TIMESTAMP(), "
                    "locked_at=NULL, last_error=NULL WHERE id=%s",
                    (),
                )
                log.info("Email job %s sent", job["id"])
            except Exception as exc:
                mark_failed(job, exc)
        except Exception:
            log.exception("Worker loop error")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
