#!/usr/bin/env python3
"""Python 3.6-compatible queue worker using mysql CLI and Brevo HTTP API."""

from __future__ import print_function

import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, "r") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


CONFIG = load_env(os.environ.get("NOTIFIER_ENV", os.path.join(BASE_DIR, ".env")))


def setting(name, default=None):
    value = os.environ.get(name, CONFIG.get(name, default))
    if value is None or value == "":
        raise RuntimeError("Missing required setting: " + name)
    return value


DB_HOST = setting("DB_HOST", "127.0.0.1")
DB_PORT = setting("DB_PORT", "3306")
DB_USER = setting("DB_USER")
DB_NAME = setting("DB_NAME", "db_gigel_prod")
DB_DEFAULTS_FILE = setting("DB_DEFAULTS_FILE", os.path.join(BASE_DIR, ".my.cnf"))
BREVO_API_KEY = setting("BREVO_API_KEY")
FROM_EMAIL = setting("FROM_EMAIL")
FROM_NAME = setting("FROM_NAME", "Gigel Account Notification")
POLL_SECONDS = max(1, int(setting("POLL_SECONDS", "5")))
MAX_ATTEMPTS = max(1, int(setting("MAX_ATTEMPTS", "8")))
MYSQL_BIN = setting("MYSQL_BIN", "mysql")
API_URL = "https://api.brevo.com/v3/smtp/email"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mysql-email-notifier")


def db(query):
    command = [
        MYSQL_BIN,
        "--defaults-extra-file=" + DB_DEFAULTS_FILE,
        "--host=" + DB_HOST,
        "--port=" + str(DB_PORT),
        "--user=" + DB_USER,
        "--database=" + DB_NAME,
        "--batch",
        "--raw",
        "--skip-column-names",
        "--execute=" + query,
    ]
    try:
        result = subprocess.check_output(command, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        error = exc.stderr or b""
        if not isinstance(error, str):
            error = error.decode("utf-8", "replace")
        detail = error.strip().splitlines()
        # MySQL CLI diagnostics do not include stored passwords. Keep only its
        # final line; the SQL text and any selected row values are never logged.
        message = detail[-1] if detail else "mysql command failed"
        raise RuntimeError("mysql CLI error: " + message)
    if not isinstance(result, str):
        result = result.decode("utf-8")
    return result.strip()


def claim_one():
    # One worker is enforced with flock below. MariaDB 10.1 has no SKIP LOCKED.
    db("UPDATE email_notification_queue SET status='pending', locked_at=NULL "
       "WHERE status='processing' AND locked_at < (UTC_TIMESTAMP() - INTERVAL 10 MINUTE)")
    result = db(
        "SELECT id, HEX(CAST(recipient_email AS BINARY)), "
        "HEX(CAST(activation_code AS BINARY)), event_type, attempts "
        "FROM email_notification_queue WHERE status='pending' "
        "AND next_attempt_at <= UTC_TIMESTAMP() ORDER BY id LIMIT 1"
    )
    if not result:
        return None
    fields = result.split("\t")
    if len(fields) != 5:
        raise RuntimeError("Unexpected queue row format")
    job = {
        "id": int(fields[0]),
        "recipient_email": bytes.fromhex(fields[1]).decode("utf-8"),
        "activation_code": bytes.fromhex(fields[2]).decode("utf-8"),
        "event_type": fields[3],
        "attempts": int(fields[4]) + 1,
    }
    db("UPDATE email_notification_queue SET status='processing', locked_at=UTC_TIMESTAMP(), "
       "attempts=attempts+1 WHERE id=" + str(job["id"]) + " AND status='pending'")
    # Confirm the claim; another worker/manual run may have claimed it first.
    confirmed = db("SELECT status FROM email_notification_queue WHERE id=" + str(job["id"]))
    if confirmed != "processing":
        return None
    return job


def send_email(job):
    event_label = "kode aktivasi baru" if job["event_type"] == "insert" else "perubahan kode aktivasi"
    body = (
        "Halo,\n\n"
        "Kami mencatat " + event_label + " untuk akun Anda.\n"
        "Kode aktivasi: " + job["activation_code"] + "\n\n"
        "Jika Anda tidak meminta perubahan ini, abaikan email ini dan hubungi administrator.\n"
    )
    payload = {
        "sender": {"name": FROM_NAME, "email": FROM_EMAIL},
        "to": [{"email": job["recipient_email"]}],
        "subject": "Notifikasi " + event_label + " akun Anda",
        "textContent": body,
    }
    data = json.dumps(payload).encode("utf-8")
    request = Request(API_URL, data=data, headers={
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        response = urlopen(request, timeout=30)
        response.read()
        response.close()
    except HTTPError as exc:
        # Do not persist provider response bodies; they may contain personal data.
        raise RuntimeError("Brevo returned HTTP " + str(exc.code))
    except URLError as exc:
        raise RuntimeError("Brevo connection failed: " + type(exc.reason).__name__)


def update_job(job_id, assignments):
    db("UPDATE email_notification_queue SET " + assignments + " WHERE id=" + str(int(job_id)))


def mark_failed(job, error):
    attempts = job["attempts"]
    if attempts >= MAX_ATTEMPTS:
        status, delay = "failed", 0
    else:
        status, delay = "pending", min(3600, 30 * (2 ** min(attempts - 1, 7)))
    error_type = type(error).__name__.replace("'", "")[:100]
    update_job(
        job["id"],
        "status='" + status + "', locked_at=NULL, last_error='" + error_type + "', "
        "next_attempt_at=UTC_TIMESTAMP() + INTERVAL " + str(delay) + " SECOND",
    )
    log.error("Email job %s failed (attempt %s/%s): %s", job["id"], attempts, MAX_ATTEMPTS, error_type)


def main():
    lock_path = os.path.join(BASE_DIR, ".worker.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.error("Another worker instance is already running")
        return 1

    log.info("Worker started")
    while True:
        try:
            job = claim_one()
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            try:
                send_email(job)
                update_job(job["id"], "status='sent', sent_at=UTC_TIMESTAMP(), locked_at=NULL, last_error=NULL")
                log.info("Email job %s sent", job["id"])
            except Exception as exc:
                mark_failed(job, exc)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc).startswith("mysql CLI error:"):
                log.error("Worker loop error: %s", exc)
            else:
                log.error("Worker loop error: %s", type(exc).__name__)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main() or 0)

