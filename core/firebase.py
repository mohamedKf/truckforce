import requests
from django.utils import timezone
from .models import NotificationLog, CompanySettings


FCM_URL = "https://fcm.googleapis.com/fcm/send"


def get_server_key():
    try:
        settings = CompanySettings.objects.first()
        return settings.firebase_server_key if settings else ''
    except Exception:
        return ''


def send_fcm_notification(token: str, title: str, body: str, data: dict = None):
    """Send a single FCM push notification."""
    server_key = get_server_key()
    if not server_key or not token:
        return False

    payload = {
        "to": token,
        "notification": {"title": title, "body": body, "sound": "default"},
        "data": data or {},
    }
    headers = {
        "Authorization": f"key={server_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(FCM_URL, json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def notify_manager_stop_skipped(manager, driver, stop):
    """Immediate notification when driver skips a stop."""
    title = "⚠️ Stop Skipped"
    body  = f"{driver.full_name} skipped: {stop.site_name}"
    data  = {
        "type":      "stop_skipped",
        "stop_id":   str(stop.id),
        "driver_id": str(driver.id),
    }
    sent = send_fcm_notification(manager.fcm_token, title, body, data)
    NotificationLog.objects.create(
        recipient_manager = manager,
        notification_type = 'stop_skipped',
        title             = title,
        body              = body,
        data              = data,
        sent              = sent,
        sent_at           = timezone.now() if sent else None,
    )


def notify_manager_day_summary(manager, driver, schedule):
    """End of day summary with missed stops."""
    missed = schedule.stops.filter(status='skipped').count()
    total  = schedule.stops.count()
    done   = schedule.stops.filter(status='done').count()
    title  = f"📋 Day Summary – {driver.full_name}"
    body   = f"Done: {done}/{total} stops. Missed: {missed}."
    data   = {
        "type":        "day_summary",
        "schedule_id": str(schedule.id),
        "driver_id":   str(driver.id),
        "missed":      str(missed),
    }
    sent = send_fcm_notification(manager.fcm_token, title, body, data)
    NotificationLog.objects.create(
        recipient_manager = manager,
        notification_type = 'day_summary',
        title             = title,
        body              = body,
        data              = data,
        sent              = sent,
        sent_at           = timezone.now() if sent else None,
    )


def notify_driver_payslip_ready(driver, payroll):
    """Tell driver their payslip is ready."""
    title = "💰 Payslip Ready"
    body  = f"Your payslip for {payroll.month}/{payroll.year} is ready."
    data  = {
        "type":       "payslip_ready",
        "payroll_id": str(payroll.id),
    }
    sent = send_fcm_notification(driver.fcm_token, title, body, data)
    NotificationLog.objects.create(
        recipient_driver  = driver,
        notification_type = 'payslip_ready',
        title             = title,
        body              = body,
        data              = data,
        sent              = sent,
        sent_at           = timezone.now() if sent else None,
    )
