#!/usr/bin/env python3
"""Delivery adapters: email (SMTP) and push (ntfy).

Credentials are NEVER stored here. Every adapter reads from environment
variables, which you populate yourself in manager/manager.env (chmod 600,
gitignored). Nothing in this repo should ever contain a password.

Adapters degrade independently: if SMTP is unconfigured, push still fires and
vice versa. A partial delivery is reported, not silently swallowed — an alert
system that fails quietly is worse than none, because you believe it is working.

Configure by writing manager/manager.env:

    # --- email ---
    MANAGER_SMTP_HOST=smtp.gmail.com
    MANAGER_SMTP_PORT=587
    MANAGER_SMTP_USER=samsonlawhon@gmail.com
    MANAGER_SMTP_PASS=<app password, not your account password>
    MANAGER_EMAIL_TO=samsonlawhon@gmail.com

    # --- push (ntfy.sh: pick an unguessable topic; anyone who knows it can read) ---
    MANAGER_NTFY_TOPIC=btf-<long-random-string>
    MANAGER_NTFY_SERVER=https://ntfy.sh
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "manager.env"


def load_env(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE lines into os.environ without overriding real env vars."""
    path = path or ENV_FILE
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


class DeliveryResult:
    def __init__(self) -> None:
        self.sent: List[str] = []
        self.skipped: Dict[str, str] = {}
        self.failed: Dict[str, str] = {}

    def as_dict(self) -> Dict[str, Any]:
        return {"sent": self.sent, "skipped": self.skipped, "failed": self.failed}

    def summary(self) -> str:
        bits = []
        if self.sent:
            bits.append("sent=" + ",".join(self.sent))
        if self.skipped:
            bits.append("skipped=" + ",".join(
                "{}({})".format(k, v) for k, v in self.skipped.items()))
        if self.failed:
            bits.append("FAILED=" + ",".join(
                "{}({})".format(k, v) for k, v in self.failed.items()))
        return " ".join(bits) or "no channels configured"


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

def send_email(subject: str, body_text: str,
               body_html: Optional[str] = None,
               result: Optional[DeliveryResult] = None) -> DeliveryResult:
    """Send mail via HTTP API if configured, else SMTP.

    The HTTP path exists because DigitalOcean blocks outbound SMTP on droplets
    by default — ports 587 and 465 both time out from the box, so no SMTP
    credential can get mail out no matter how it is configured. An HTTPS API
    call on 443 is unaffected. The Mac has no such block and keeps using SMTP.
    """
    result = result or DeliveryResult()
    if os.environ.get("MANAGER_RESEND_KEY"):
        return send_email_http(subject, body_text, body_html, result)
    host = os.environ.get("MANAGER_SMTP_HOST")
    user = os.environ.get("MANAGER_SMTP_USER")
    # Google displays app passwords as "abcd efgh ijkl mnop". The spaces are
    # presentational — SMTP auth fails if they are sent literally, and the
    # resulting error ("Username and Password not accepted") points you at the
    # wrong problem entirely.
    pw = (os.environ.get("MANAGER_SMTP_PASS") or "").replace(" ", "").strip()
    to = os.environ.get("MANAGER_EMAIL_TO") or user

    if not (host and user and pw and to):
        result.skipped["email"] = "not configured"
        return result

    port = int(os.environ.get("MANAGER_SMTP_PORT", "587"))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=45) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(user, pw)
                s.send_message(msg)
        result.sent.append("email")
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        result.failed["email"] = "{}: {}".format(type(exc).__name__, exc)
    return result


def send_email_http(subject: str, body_text: str,
                    body_html: Optional[str] = None,
                    result: Optional[DeliveryResult] = None) -> DeliveryResult:
    """Send via the Resend HTTP API (https://api.resend.com/emails)."""
    result = result or DeliveryResult()
    key = os.environ.get("MANAGER_RESEND_KEY")
    to = os.environ.get("MANAGER_EMAIL_TO")
    # Until a domain is verified, Resend only allows onboarding@resend.dev as
    # the sender, and only to the account's own address.
    sender = os.environ.get("MANAGER_EMAIL_FROM", "onboarding@resend.dev")

    if not (key and to):
        result.skipped["email"] = "resend key or recipient missing"
        return result

    payload = {"from": sender, "to": [to], "subject": subject, "text": body_text}
    if body_html:
        payload["html"] = body_html
    # A verified domain authorises SENDING from any address at it, but nothing
    # receives there. Without a Reply-To, hitting reply on the brief silently
    # goes nowhere.
    reply_to = os.environ.get("MANAGER_EMAIL_REPLY_TO") or os.environ.get("MANAGER_EMAIL_TO")
    if reply_to and reply_to != sender:
        payload["reply_to"] = reply_to

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 # api.resend.com sits behind Cloudflare, which bans urllib's
                 # default "Python-urllib/3.x" agent outright (403, error 1010).
                 # The key is fine; the request never reaches Resend without a
                 # plausible User-Agent.
                 "User-Agent": "betting-fund-manager/1.0",
                 "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                result.sent.append("email(http)")
            else:
                result.failed["email"] = "HTTP {}".format(resp.status)
    except urllib.error.HTTPError as exc:
        # Resend returns a JSON body explaining the rejection — surfacing it is
        # the difference between a 2-minute fix and an afternoon of guessing.
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            detail = ""
        result.failed["email"] = "HTTP {} {}".format(exc.code, detail)
    except (urllib.error.URLError, OSError) as exc:
        result.failed["email"] = "{}: {}".format(type(exc).__name__, exc)
    return result


# --------------------------------------------------------------------------
# push (ntfy)
# --------------------------------------------------------------------------

_PRIORITY = {"critical": "urgent", "warn": "high", "action": "default",
             "info": "low"}


def send_push(title: str, body: str, severity: str = "warn",
              click_url: Optional[str] = None,
              result: Optional[DeliveryResult] = None) -> DeliveryResult:
    result = result or DeliveryResult()
    topic = os.environ.get("MANAGER_NTFY_TOPIC")
    if not topic:
        result.skipped["push"] = "not configured"
        return result

    server = os.environ.get("MANAGER_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    url = "{}/{}".format(server, topic)
    headers = {
        "Title": title[:200].encode("utf-8", "replace").decode("ascii", "replace"),
        "Priority": _PRIORITY.get(severity, "default"),
        "Tags": {"critical": "rotating_light", "warn": "warning",
                 "action": "memo"}.get(severity, "information_source"),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click_url:
        headers["Click"] = click_url
    token = os.environ.get("MANAGER_NTFY_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token

    req = urllib.request.Request(url, data=body[:3500].encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                result.sent.append("push")
            else:
                result.failed["push"] = "HTTP {}".format(resp.status)
    except (urllib.error.URLError, OSError) as exc:
        result.failed["push"] = "{}: {}".format(type(exc).__name__, exc)
    return result


def selftest() -> int:
    """Verify configured channels actually deliver. Run this once after setup —
    an untested alert path is an assumption, not a safety net."""
    load_env()
    res = DeliveryResult()
    send_email("[betting-fund] manager selftest",
               "If you are reading this, email delivery works.\n"
               "Sent by manager/notify.py --selftest.", result=res)
    send_push("manager selftest", "Push delivery works.", "info", result=res)
    print(json.dumps(res.as_dict(), indent=2))
    if res.failed:
        return 1
    if not res.sent:
        print("\nNo channels configured. Create manager/manager.env "
              "(see the docstring at the top of this file).")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
