from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from core import views as core_views   # NEW — import core views for the public tracking page

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
    path('api/health/', lambda r: JsonResponse({'status': 'ok', 'service': 'TruckForce'})),

    # Public tracking page — served at the root so clients get a clean URL
    # like https://truckforce-production.up.railway.app/track/<token>/
    # without the /api prefix. The JSON endpoints that this page hits live
    # under /api/track/<token>/... and are registered in core/urls.py.
    path('track/<str:token>/', core_views.tracking_page, name='tracking_page'),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)