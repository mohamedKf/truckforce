"""
core/firebase_sync.py — Publishes change events to Firestore for real-time sync.

Separate from firebase.py (which handles FCM push to mobile apps).
This module writes small "something changed" events to Firestore that
desktop apps listen to for instant cross-user refresh.
"""

import os
import threading
from datetime import datetime, timezone

# Firebase SDK imports (lazy — only if available)
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False
    print("[FIREBASE-SYNC] firebase_admin not installed — real-time sync disabled", flush=True)


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════
# Path to the credentials JSON — either in project root or configurable via env var
_CREDENTIALS_FILENAME = 'firebase-credentials.json'
# Tenant id identifies WHICH client this Django serves (each client VPS has its own)
# Override via env var TRUCKFORCE_TENANT_ID in each client's deployment
_TENANT_ID = os.environ.get('TRUCKFORCE_TENANT_ID', 'default')


# ═══════════════════════════════════════════════════════════════
# Lazy singleton init
# ═══════════════════════════════════════════════════════════════
_db = None
_init_lock = threading.Lock()
_init_failed = False


def _get_db():
    """Initialize Firebase on first use. Returns Firestore client or None on failure."""
    global _db, _init_failed
    if _init_failed or not _FIREBASE_AVAILABLE:
        return None
    if _db is not None:
        return _db
    with _init_lock:
        if _db is not None:
            return _db
        try:
            # Look for credentials file next to manage.py (project root)
            from django.conf import settings
            cred_path = os.path.join(str(settings.BASE_DIR), _CREDENTIALS_FILENAME)
            if not os.path.exists(cred_path):
                print(f"[FIREBASE-SYNC] Credentials file not found at {cred_path} — "
                      f"real-time sync disabled", flush=True)
                _init_failed = True
                return None
            cred = credentials.Certificate(cred_path)
            # Avoid re-initializing if already done
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            _db = firestore.client()
            print(f"[FIREBASE-SYNC] ✓ Connected (tenant: {_TENANT_ID})", flush=True)
            return _db
        except Exception as e:
            print(f"[FIREBASE-SYNC] Init failed: {e}", flush=True)
            _init_failed = True
            return None


# ═══════════════════════════════════════════════════════════════
# Public API — call publish_event() after any mutation
# ═══════════════════════════════════════════════════════════════

# Valid event types — must match what desktop listener handles
EVENT_TYPES = {
    'drivers_changed',
    'trucks_changed',
    'schedules_changed',
    'stops_changed',
    'stop_arrived',
    'payslips_changed',
    'attendance_changed',
    'settings_changed',
}


def publish_event(event_type: str, payload: dict = None, by_user_id: int = None):
    """
    Write an event doc to Firestore. Called from views after any DB mutation.
    Fire-and-forget: runs in background thread, never blocks the HTTP response.

    Example:
        publish_event('drivers_changed', by_user_id=request.manager.id)
        publish_event('stop_arrived', payload={'stop_id': 42})
    """
    if event_type not in EVENT_TYPES:
        print(f"[FIREBASE-SYNC] Unknown event type: {event_type}", flush=True)
        return

    def _write():
        db = _get_db()
        if db is None:
            return
        try:
            doc = {
                'type':       event_type,
                'tenant_id':  _TENANT_ID,
                'at':         datetime.now(timezone.utc),
                'by_user_id': by_user_id,
                'payload':    payload or {},
            }
            db.collection('events').add(doc)
        except Exception as e:
            print(f"[FIREBASE-SYNC] Publish failed ({event_type}): {e}", flush=True)

    # Non-blocking — spawn thread so slow network doesn't delay HTTP response
    threading.Thread(target=_write, daemon=True).start()