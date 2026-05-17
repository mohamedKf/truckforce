"""
FCM v1 API client + notification helpers.

Replaces the legacy "server key" approach (deprecated by Google on June 20,
2024 and now returns 404). Uses a Google service-account JSON to mint
short-lived OAuth2 access tokens and post to the v1 endpoint.

Configuration: set the env var FIREBASE_SERVICE_ACCOUNT_JSON to the entire
contents of the service-account JSON file (paste it as a single line —
Railway accepts multi-line just fine too). Alternative for local dev:
place the file at PROJECT_ROOT/firebase-service-account.json.

Project ID is read from the JSON itself; no separate env var needed.
"""
import os
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import requests
from django.utils import timezone

from .models import NotificationLog


# ─── Module-level state ────────────────────────────────────────────────
# We cache the OAuth2 access token in memory for ~50 minutes (tokens last
# 60 min; we refresh slightly early to avoid race conditions). A lock
# prevents multiple workers from minting tokens simultaneously.
_token_lock        = threading.Lock()
_cached_token      = None        # type: Optional[str]
_cached_token_exp  = None        # type: Optional[datetime]
_cached_project_id = None        # type: Optional[str]


# ─── Credentials loading ───────────────────────────────────────────────

def _load_service_account() -> Optional[Dict[str, Any]]:
    """Return service-account dict, or None if not configured.

    Tries (in order):
    1. FIREBASE_CREDENTIALS_JSON env var (raw JSON string) — preferred name,
       matches what settings.py already reads.
    2. FIREBASE_SERVICE_ACCOUNT_JSON env var — alias kept for compatibility.
    3. The file path Django writes from FIREBASE_CREDENTIALS_JSON, exposed
       on `settings.FIREBASE_CREDENTIALS_PATH`. This avoids re-parsing JSON
       twice if settings.py already loaded it.
    4. firebase-service-account.json file in CWD (local dev).
    """
    raw = (os.environ.get('FIREBASE_CREDENTIALS_JSON')
           or os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON'))
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[FCM] FIREBASE_CREDENTIALS_JSON is malformed: {e}", flush=True)
            return None

    # Reuse the path settings.py already set up if available.
    try:
        from django.conf import settings as _settings
        path = getattr(_settings, 'FIREBASE_CREDENTIALS_PATH', None)
        if path and os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass

    for path in ('firebase-service-account.json',
                 '/app/firebase-service-account.json'):
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[FCM] Failed reading {path}: {e}", flush=True)

    return None


# ─── OAuth2 token ──────────────────────────────────────────────────────

def _get_access_token() -> Optional[str]:
    """Return a valid OAuth2 access token for FCM v1, minting if needed.

    Uses google.oauth2.service_account so we don't have to hand-roll JWT
    signing. The google-auth package is already a transitive dependency
    of firebase_admin which you have installed.
    """
    global _cached_token, _cached_token_exp, _cached_project_id

    with _token_lock:
        # Cache hit — return existing token if still valid
        if (_cached_token
                and _cached_token_exp
                and datetime.utcnow() < _cached_token_exp):
            return _cached_token

        sa = _load_service_account()
        if not sa:
            print("[FCM] No service account configured — push disabled", flush=True)
            return None

        _cached_project_id = sa.get('project_id')

        try:
            # Import here so the module loads cleanly even without the
            # google-auth package installed (in which case push silently
            # disables itself rather than crashing the whole server).
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GAuthRequest

            creds = service_account.Credentials.from_service_account_info(
                sa,
                scopes=['https://www.googleapis.com/auth/firebase.messaging'],
            )
            creds.refresh(GAuthRequest())
            _cached_token = creds.token
            # creds.expiry is naive UTC; subtract 10 min buffer
            _cached_token_exp = creds.expiry - timedelta(minutes=10)
            return _cached_token
        except Exception as e:
            print(f"[FCM] Token mint failed: {e}", flush=True)
            return None


def _get_project_id() -> Optional[str]:
    """Project ID is loaded along with the token; ensure both are fresh."""
    if _cached_project_id is None:
        _get_access_token()
    return _cached_project_id


# ─── Core send function ────────────────────────────────────────────────

def send_fcm_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send a single FCM v1 push notification. Returns True on 2xx.

    FCM v1 differs from legacy:
    - Endpoint includes project_id
    - Auth header is "Bearer <oauth_token>" not "key=<server_key>"
    - Payload is wrapped in {"message": {...}}
    - Data values must all be strings (no ints, bools, None)
    """
    if not token:
        return False

    access_token = _get_access_token()
    project_id = _get_project_id()
    if not access_token or not project_id:
        return False

    # FCM v1 requires every data value to be a string. Drop None and
    # stringify everything else so callers don't have to think about it.
    safe_data = {}
    for k, v in (data or {}).items():
        if v is None:
            continue
        safe_data[str(k)] = str(v)

    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body":  body,
            },
            "data": safe_data,
            # Android-specific: high priority + default sound + open the
            # app when the user taps the notification.
            "android": {
                "priority": "high",
                "notification": {
                    "sound":         "default",
                    "click_action":  "FLUTTER_NOTIFICATION_CLICK",
                    "channel_id":    "truckforce_notifications",
                },
            },
            # iOS-specific (harmless if you don't have iOS yet)
            "apns": {
                "payload": {
                    "aps": {
                        "sound": "default",
                    }
                }
            },
        }
    }

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; UTF-8",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        ok = 200 <= resp.status_code < 300
        if not ok:
            # Common failures we want visibility on:
            # 404 UNREGISTERED → token is dead, driver uninstalled app
            # 400 INVALID_ARGUMENT → payload shape wrong
            # 401/403 → service account perms or token issue
            print(f"[FCM] send failed status={resp.status_code} "
                  f"body={resp.text[:300]}", flush=True)
            _maybe_invalidate_token(token, resp)
        return ok
    except requests.RequestException as e:
        print(f"[FCM] network error: {e}", flush=True)
        return False


def _maybe_invalidate_token(token: str, resp: requests.Response) -> None:
    """If FCM tells us the token is permanently dead, clear it from the DB.

    A 404 with error type UNREGISTERED or NOT_FOUND means the user
    uninstalled the app, cleared its data, or the token was reissued.
    Clearing the field stops us from retrying it on every notification.
    """
    if resp.status_code != 404:
        return
    try:
        err = resp.json().get('error', {}).get('details', [])
        codes = [d.get('errorCode') for d in err if isinstance(d, dict)]
        if 'UNREGISTERED' in codes or resp.status_code == 404:
            # Lazy import to avoid circular import at module load
            from .models import Driver, Manager
            Driver.objects.filter(fcm_token=token).update(fcm_token='')
            Manager.objects.filter(fcm_token=token).update(fcm_token='')
            print(f"[FCM] invalidated stale token", flush=True)
    except Exception:
        pass


# ─── Higher-level notification helpers ─────────────────────────────────
# These wrap send_fcm_notification + NotificationLog logging. The caller
# never has to think about either.

def _log_and_send_driver(
    driver, notification_type: str, title: str, body: str, data: dict
) -> bool:
    sent = send_fcm_notification(driver.fcm_token, title, body, data)
    NotificationLog.objects.create(
        recipient_driver  = driver,
        notification_type = notification_type,
        title             = title,
        body              = body,
        data              = data,
        sent              = sent,
        sent_at           = timezone.now() if sent else None,
    )
    return sent


def _log_and_send_manager(
    manager, notification_type: str, title: str, body: str, data: dict
) -> bool:
    sent = send_fcm_notification(manager.fcm_token, title, body, data)
    NotificationLog.objects.create(
        recipient_manager = manager,
        notification_type = notification_type,
        title             = title,
        body              = body,
        data              = data,
        sent              = sent,
        sent_at           = timezone.now() if sent else None,
    )
    return sent


# ─── Existing notifications (preserved API, now actually delivering) ───

def notify_manager_stop_skipped(manager, driver, stop) -> bool:
    title = "⚠️ עצירה דולגה"
    body  = f"{driver.full_name} דילג על: {stop.site_name}"
    data  = {
        "type":      "stop_skipped",
        "stop_id":   stop.id,
        "driver_id": driver.id,
    }
    return _log_and_send_manager(manager, 'stop_skipped', title, body, data)


def notify_manager_day_summary(manager, driver, schedule) -> bool:
    missed = schedule.stops.filter(status='skipped').count()
    total  = schedule.stops.count()
    done   = schedule.stops.filter(status='done').count()
    title  = f"📋 סיכום יום – {driver.full_name}"
    body   = f"בוצעו: {done}/{total} עצירות. דולגו: {missed}."
    data   = {
        "type":        "day_summary",
        "schedule_id": schedule.id,
        "driver_id":   driver.id,
        "missed":      missed,
    }
    return _log_and_send_manager(manager, 'day_summary', title, body, data)


def notify_driver_payslip_ready(driver, payroll) -> bool:
    title = "💰 תלוש שכר מוכן"
    body  = f"תלוש השכר שלך לחודש {payroll.month}/{payroll.year} מוכן."
    data  = {
        "type":       "payslip_ready",
        "payroll_id": payroll.id,
    }
    return _log_and_send_driver(driver, 'payslip_ready', title, body, data)


# ─── New: schedule notifications ───────────────────────────────────────

def notify_driver_schedule_assigned(driver, schedule) -> bool:
    """Driver receives a new daily schedule (first time today)."""
    stops_count = schedule.stops.count()
    date_str = schedule.date.strftime('%d/%m/%Y')
    title = "🗓️ סידור עבודה חדש"
    # Hebrew has 3 grammatical numbers in casual use: 1 / 2 / many.
    # We use simple binary singular/plural since "1 עצירה" vs "N עצירות"
    # is the standard distinction drivers will see.
    word = "עצירה" if stops_count == 1 else "עצירות"
    body  = f"יש לך {stops_count} {word} לתאריך {date_str}"
    data  = {
        "type":        "schedule_assigned",
        "schedule_id": schedule.id,
        "date":        schedule.date.isoformat(),
        "stops_count": stops_count,
    }
    return _log_and_send_driver(driver, 'schedule_changed', title, body, data)


def notify_driver_schedule_updated(driver, schedule, change_summary: str) -> bool:
    """Driver's existing schedule was modified (stops added/removed/changed)."""
    date_str = schedule.date.strftime('%d/%m/%Y')
    title = "🔄 סידור העבודה עודכן"
    body  = f"הסידור שלך לתאריך {date_str}: {change_summary}"
    data  = {
        "type":        "schedule_updated",
        "schedule_id": schedule.id,
        "date":        schedule.date.isoformat(),
    }
    return _log_and_send_driver(driver, 'schedule_changed', title, body, data)