from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse

from core import views as core_views

urlpatterns = [
    path('admin/',         admin.site.urls),
    path('api/',           include('core.urls')),
    path('api/health/',    lambda r: JsonResponse({'status': 'ok', 'service': 'TruckForce'})),

    # Public HTML tracking page — shared with clients, no auth.
    path('track/<str:token>/', core_views.tracking_page, name='tracking-page'),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)