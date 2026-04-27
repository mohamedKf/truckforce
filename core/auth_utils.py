import secrets
from django.core.cache import cache


TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days


def generate_token():
    return secrets.token_hex(32)


def store_manager_token(token, manager_id):
    cache.set(f"manager_token_{token}", manager_id, TOKEN_TTL)


def store_driver_token(token, driver_id):
    cache.set(f"driver_token_{token}", driver_id, TOKEN_TTL)


def get_manager_from_token(token):
    from .models import Manager
    manager_id = cache.get(f"manager_token_{token}")
    if manager_id:
        try:
            return Manager.objects.get(id=manager_id, is_active=True)
        except Manager.DoesNotExist:
            pass
    return None


def get_driver_from_token(token):
    from .models import Driver
    driver_id = cache.get(f"driver_token_{token}")
    if driver_id:
        try:
            return Driver.objects.get(id=driver_id, is_active=True)
        except Driver.DoesNotExist:
            pass
    return None


def revoke_token(token, role='manager'):
    prefix = 'manager' if role == 'manager' else 'driver'
    cache.delete(f"{prefix}_token_{token}")
