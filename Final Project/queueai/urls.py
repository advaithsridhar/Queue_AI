from django.urls import path
from . import views

app_name = 'queueai'

urlpatterns = [
    # ── Frontend ───────────────────────────────────────────────────────
    path('',        views.index,        name='index'),
    path('queue',   views.queue_detail, name='queue_detail'),

    # ── Admin Panel ────────────────────────────────────────────────────
    # Both trailing and non-trailing slash versions for compatibility
    path('admin/login/',   views.admin_login_view,  name='admin_login'),
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/logout/',  views.admin_logout_view, name='admin_logout'),
    
    path('admin-login',    views.admin_login_view),
    path('admin',          views.admin_dashboard),
    path('admin-logout',   views.admin_logout_view),

    # ── REST API ───────────────────────────────────────────────────────
    # Both trailing and non-trailing slash versions for POST endpoints
    path('api/manual-update/',     views.api_manual_update,      name='api_manual_update'),
    path('api/manual-update',       views.api_manual_update),
    path('api/presentation-mode/', views.api_presentation_mode,  name='api_presentation_mode'),
    path('api/presentation-mode',   views.api_presentation_mode),
    
    path('api/live-queue',          views.api_live_queue,          name='api_live_queue'),
    path('api/chart-history',       views.api_chart_history,       name='api_chart_history'),
    
    # Dual-format Calibration endpoint
    path('api/calibrate/',         views.api_calibrate,           name='api_calibrate'),
    path('api/calibrate',           views.api_calibrate),

    # Ingest and legacy sensor endpoints
    path('api/ingest/',            views.api_ingest,              name='api_ingest'),
    path('api/ingest',              views.api_ingest),
    path('api/zones',              views.api_zones,               name='api_zones'),
    path('api/history/<str:zone_id>', views.api_history_by_zone,   name='api_history_by_zone'),
    path('api/predict/<str:zone_id>', views.api_predict_by_zone,   name='api_predict_by_zone'),
    path('api/health',             views.api_health,              name='api_health'),
]
