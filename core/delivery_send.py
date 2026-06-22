"""
delivery_send.py — Send (or re-send) a stop's signed delivery note to the
client on demand, by EMAIL or by WhatsApp.

Design (chosen with the user):
  • EMAIL    — the server emails the signed confirmation PDF as an attachment
               (fetched over HTTP from Cloudinary, never via `.path`).
  • WHATSAPP — Option A "deep link": the server does NOT send the message
               itself. It returns a ready wa.me URL (client phone + a message
               containing the PDF link) that the caller (driver app or desktop)
               opens, so the human taps Send in their own WhatsApp. Free, no
               WhatsApp Business account, no per-message cost.

Either way, the email/phone the user confirms is saved back onto the Stop
(stop.contact_email / stop.contact_phone) AND recorded on the confirmation
(signed_by_email / signed_by_phone), so it persists for next time.

Entry point:
  send_note(stop, channel, email, phone, request=None) -> (http_status, payload)
"""

import urllib.parse

import requests
from django.conf import settings
from django.core.mail import EmailMessage


# ── helpers ───────────────────────────────────────────────

def _pdf_url(filefield, request=None):
    """Public URL of a (Cloudinary) FileField. Falls back to an absolute URL
    built from the request for local storage. '' if nothing is stored."""
    if not filefield:
        return ''
    try:
        url = filefield.url
    except Exception:
        url = str(getattr(filefield, 'name', '') or '')
    if url and not url.startswith('http') and request is not None:
        try:
            url = request.build_absolute_uri(url)
        except Exception:
            pass
    return url or ''


def _pdf_bytes(filefield):
    """Bytes of a (Cloudinary) PDF FileField, fetched over HTTP. None on
    failure. NEVER uses `.path` — that raises on Cloudinary storage."""
    if not filefield:
        return None
    name = str(getattr(filefield, 'name', '') or '')
    url = name if name.startswith('http') else ''
    if not url:
        try:
            url = filefield.url
        except Exception:
            url = ''
    if not url or not url.startswith('http'):
        return None
    try:
        r = requests.get(url, timeout=60)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _il_phone(phone):
    """Normalize an Israeli phone to international digits for wa.me
    (e.g. 050-123-4567 -> 972501234567)."""
    p = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if p.startswith('00'):
        p = p[2:]
    if p.startswith('0'):
        p = '972' + p[1:]
    elif not p.startswith('972'):
        p = '972' + p
    return p


def _wa_message(conf, pdf_url):
    """The WhatsApp text: a short Hebrew note plus the link to the signed PDF."""
    site = (conf.stop.site_name or '').strip()
    # "שלום, מצורף אישור מסירה" (+ " עבור <site>")
    msg = '\u05e9\u05dc\u05d5\u05dd, \u05de\u05e6\u05d5\u05e3 \u05d0\u05d9\u05e9\u05d5\u05e8 \u05de\u05e1\u05d9\u05e8\u05d4'
    if site:
        msg += ' \u05e2\u05d1\u05d5\u05e8 ' + site
    if pdf_url:
        msg += ':\n' + pdf_url
    return msg


def _wa_me_link(phone, text):
    """Build a wa.me deep link the caller opens (WhatsApp app / WhatsApp Web)."""
    return ('https://wa.me/' + _il_phone(phone)
            + '?text=' + urllib.parse.quote(text))


def _company_from_email():
    """From-address for outgoing notes = the company's own email
    (CompanySettings.email), shown as 'Company Name <email>'. Falls back to
    settings.DEFAULT_FROM_EMAIL.

    IMPORTANT: the account we AUTHENTICATE with is still the env
    EMAIL_HOST_USER. For Gmail not to rewrite or spam-flag the From, that env
    account must BE this same company email (or have it as a verified
    'Send mail as' alias). Same address in CompanySettings.email and
    EMAIL_HOST_USER = legitimate; different = spoofing → blocked."""
    cs = None
    try:
        from .models import CompanySettings
        cs = CompanySettings.objects.first()
    except Exception:
        cs = None
    email = (getattr(cs, 'email', '') or '').strip() if cs else ''
    name = (getattr(cs, 'company_name', '') or '').strip() if cs else ''
    if not email:
        return getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    return ('%s <%s>' % (name, email)) if name else email


def email_delivery_note(conf, to_email):
    """Email the signed confirmation PDF to to_email. True on success.
    Best-effort: never raises."""
    try:
        stop = conf.stop
        subject = 'Delivery Confirmation — ' + (stop.site_name or '')
        try:
            date_str = str(stop.schedule.date)
        except Exception:
            date_str = ''
        body = (
            'Dear ' + (conf.signed_by_name or 'customer') + ',\n\n'
            'Please find attached the signed delivery confirmation.\n'
            'Site: ' + (stop.site_name or '') + '\n'
            'Address: ' + (stop.address or '') + '\n'
            + ('Date: ' + date_str + '\n' if date_str else '')
            + '\nThank you.'
        )
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=_company_from_email(),
            to=[to_email],
        )
        pdf = _pdf_bytes(conf.pdf_file)
        if pdf:
            msg.attach('delivery_confirmation_%s.pdf' % conf.stop_id,
                       pdf, 'application/pdf')
        msg.send()
        print('[NOTE-SEND] emailed stop=%s to=%s pdf=%s'
              % (conf.stop_id, to_email, 'yes' if pdf else 'no'), flush=True)
        return True
    except Exception as e:
        print('[NOTE-SEND] email error stop=%s: %s' % (conf.stop_id, e),
              flush=True)
        return False


# ── orchestrator ──────────────────────────────────────────

def send_note(stop, channel, email, phone, request=None):
    """Save confirmed contact onto the stop, then send by `channel`.
    Returns (http_status, payload_dict)."""
    conf = getattr(stop, 'confirmation', None)
    if conf is None:
        return 404, {'error': 'no_confirmation'}

    channel = (channel or '').strip().lower()
    email = (email or '').strip()
    phone = (phone or '').strip()

    # Persist the confirmed contact info onto the stop + the confirmation.
    stop_fields, conf_fields = [], []
    if email:
        stop.contact_email = email
        conf.signed_by_email = email
        stop_fields.append('contact_email')
        conf_fields.append('signed_by_email')
    if phone:
        stop.contact_phone = phone
        conf.signed_by_phone = phone
        stop_fields.append('contact_phone')
        conf_fields.append('signed_by_phone')
    if stop_fields:
        stop.save(update_fields=stop_fields)

    pdf_url = _pdf_url(conf.pdf_file, request)

    if channel == 'email':
        if not email:
            return 400, {'error': 'email_required'}
        if email_delivery_note(conf, email):
            conf.email_sent = True
            conf.save(update_fields=conf_fields + ['email_sent'])
            return 200, {'ok': True}
        if conf_fields:
            conf.save(update_fields=conf_fields)
        return 502, {'error': 'send_failed'}

    if channel == 'whatsapp':
        if not phone:
            return 400, {'error': 'phone_required'}
        wa = _wa_me_link(phone, _wa_message(conf, pdf_url))
        conf.whatsapp_sent = True
        conf.save(update_fields=conf_fields + ['whatsapp_sent'])
        return 200, {'ok': True, 'whatsapp_url': wa, 'pdf_url': pdf_url}

    return 400, {'error': 'bad_channel'}