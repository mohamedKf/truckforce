from rest_framework.permissions import BasePermission
from .auth_utils import get_manager_from_token, get_driver_from_token


def get_token_from_request(request):
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Token '):
        return auth_header.split(' ')[1]
    return None


class IsManager(BasePermission):
    """Only authenticated managers can access."""
    def has_permission(self, request, view):
        token = get_token_from_request(request)
        if not token:
            return False
        manager = get_manager_from_token(token)
        if manager:
            request.manager = manager
            return True
        return False


class IsAdmin(BasePermission):
    """Only managers with role=admin."""
    def has_permission(self, request, view):
        token = get_token_from_request(request)
        if not token:
            return False
        manager = get_manager_from_token(token)
        if manager and manager.role == 'admin':
            request.manager = manager
            return True
        return False


class IsDriver(BasePermission):
    """Only authenticated drivers can access."""
    def has_permission(self, request, view):
        token = get_token_from_request(request)
        if not token:
            return False
        driver = get_driver_from_token(token)
        if driver:
            request.driver = driver
            return True
        return False


class IsManagerOrDriver(BasePermission):
    """Either manager or driver – sets request.manager or request.driver."""
    def has_permission(self, request, view):
        token = get_token_from_request(request)
        if not token:
            return False
        manager = get_manager_from_token(token)
        if manager:
            request.manager = manager
            request.driver  = None
            return True
        driver = get_driver_from_token(token)
        if driver:
            request.driver  = driver
            request.manager = None
            return True
        return False
