# queueai/urls.py
from django.urls import path
from . import views

app_name = 'queueai'

urlpatterns = [
    # ── Frontend ───────────────────────────────────────────────────────
    path('',        views.index,        name='index'),
    path('queue',   views.queue_detail, name='queue_detail'),

    # ── Admin Panel ────────────────────────────────────────────────────
    path('admin-login',    views.admin_login_view,  name='admin_login'),
    path('admin',          views.admin_dashboard,   name='admin_dashboard'),
    path('admin-logout',   views.admin_logout_view, name='admin_logout'),

    # ── REST API ───────────────────────────────────────────────────────
    path('api/manual-update',       views.api_manual_update,      name='api_manual_update'),
    path('api/live-queue',          views.api_live_queue,          name='api_live_queue'),
    path('api/presentation-mode',   views.api_presentation_mode,  name='api_presentation_mode'),
    path('api/chart-history',       views.api_chart_history,       name='api_chart_history'),
    path('api/calibrate',           views.api_calibrate,           name='api_calibrate'),
]


# ── Root project urls.py (project/urls.py) ─────────────────────────────
# Add this to your main project/urls.py:
#
#   from django.urls import path, include
#
#   urlpatterns = [
#       path('',        include('queueai.urls')),
#       path('django-admin/', admin.site.urls),  # keep Django's own admin
#   ]


# ── WebSocket routing (project/routing.py) ─────────────────────────────
# Create this file at the project level:
#
#   from django.urls import re_path
#   from queueai.consumers import QueueConsumer
#
#   websocket_urlpatterns = [
#       re_path(
#           r'ws/queue/(?P<location>[^/]+)/(?P<block>[^/]+)/$',
#           QueueConsumer.as_asgi()
#       ),
#   ]


# ── ASGI entry point (project/asgi.py) ────────────────────────────────
ASGI_CODE = """
# project/asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from .routing import websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
"""
