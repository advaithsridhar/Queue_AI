# queueai/views.py
import json
import logging
from datetime import timedelta
import statistics

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Avg

from .models import (
    Place, QueueBlock, QueueSnapshot,
    ManualUpdate, PresentationConfig, CalibrationRecord
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS & CONFIG
# ─────────────────────────────────────────────────────────────
SMOOTHING_WINDOW = 5
SMOOTHING_ALPHA = 0.4

ZONE_CONFIG = {
    "entrance-1": {"avg_service_time": 45, "num_servers": 2, "medium_at": 8, "high_at": 20},
    "default":    {"avg_service_time": 60, "num_servers": 1, "medium_at": 5, "high_at": 15},
}

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def is_admin(user):
    return user.is_authenticated and user.is_staff


def _level_from_count(count: int) -> str:
    if count <= 3:
        return 'LOW'
    elif count <= 8:
        return 'MEDIUM'
    return 'HIGH'


def _wait_from_count(count: int) -> float:
    """Simple linear wait time estimate (minutes)."""
    return round(count * 0.4, 1)


def ewma(values_oldest_first: list, alpha: float = SMOOTHING_ALPHA) -> float:
    """Exponential moving average. `values_oldest_first[-1]` is the newest."""
    if not values_oldest_first:
        return 0.0
    result = values_oldest_first[0]
    for v in values_oldest_first[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def _get_live_data(place_name: str, block_name: str) -> dict:
    """
    Return the most recent data for a place/block.
    If PRESENTATION_MODE is on → return latest ManualUpdate.
    Otherwise → return latest QueueSnapshot.
    """
    from django.conf import settings
    config = PresentationConfig.get()

    presentation_on = getattr(settings, 'PRESENTATION_MODE', False) or config.is_enabled

    if presentation_on:
        # Use admin-entered values
        update = ManualUpdate.objects.filter(
            place__icontains=place_name,
            block__icontains=block_name
        ).first()
        if update:
            return {
                'crowd_count': update.crowd_count,
                'wait_time':   update.wait_time,
                'crowd_level': update.crowd_level,
                'source':      'manual',
                'presentation_mode': True,
                'timestamp': update.submitted_at.isoformat(),
            }

    # Sensor / ML pipeline
    try:
        block = QueueBlock.objects.get(
            place__name__icontains=place_name,
            name__icontains=block_name,
            is_active=True
        )
        snapshot = block.snapshots.first()
        if snapshot:
            return {
                'crowd_count': snapshot.crowd_count,
                'wait_time':   snapshot.wait_time_min,
                'crowd_level': snapshot.crowd_level,
                'source':      snapshot.source,
                'presentation_mode': False,
                'timestamp': snapshot.timestamp.isoformat(),
            }
    except QueueBlock.DoesNotExist:
        pass

    return {
        'crowd_count': 0,
        'wait_time':   0,
        'crowd_level': 'LOW',
        'source':      'none',
        'presentation_mode': presentation_on,
        'timestamp': timezone.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# MAIN FRONTEND (serves index.html)
# ─────────────────────────────────────────────────────────────

def index(request):
    """Serve the QueueAI SPA."""
    config = PresentationConfig.get()
    places = Place.objects.filter(is_active=True).prefetch_related('blocks')
    context = {
        'places': places,
        'presentation_mode': config.is_enabled,
    }
    return render(request, 'queueai/index.html', context)


def queue_detail(request):
    """Serve the queue detail page with live data injected."""
    location  = request.GET.get('location', '')
    block_name = request.GET.get('block', '')
    live_data  = _get_live_data(location, block_name)
    context = {
        'location': location,
        'block_name': block_name,
        'live_data': live_data,
        'presentation_mode': live_data.get('presentation_mode', False),
    }
    return render(request, 'queueai/queue.html', context)


# ─────────────────────────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────────────────────────

def admin_login_view(request):
    """Django Database authentication admin login."""
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            # Set session variables for backward compatibility with templates and other endpoints
            request.session['admin_logged_in'] = True
            request.session['admin_username'] = username
            return redirect('queueai:admin_dashboard')
        return render(request, 'queueai/admin_login.html', {'error': 'Invalid credentials'})
    return render(request, 'queueai/admin_login.html')


def admin_required(view_func):
    """Custom decorator checking both Django auth and session flag."""
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated or request.session.get('admin_logged_in')):
            return redirect('queueai:admin_login')
        return view_func(request, *args, **kwargs)
    return wrapper


@admin_required
def admin_dashboard(request):
    """Admin dashboard with manual update form."""
    config   = PresentationConfig.get()
    updates  = ManualUpdate.objects.all()[:20]
    places   = Place.objects.filter(is_active=True).prefetch_related('blocks')
    context  = {
        'config': config,
        'updates': updates,
        'places': places,
    }
    return render(request, 'queueai/admin_dashboard.html', context)


@admin_required
def admin_logout_view(request):
    request.session.flush()
    logout(request)
    return redirect('queueai:admin_login')


# ─────────────────────────────────────────────────────────────
# REST API
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def api_manual_update(request):
    """
    POST /api/manual-update
    """
    if not (request.user.is_authenticated or request.session.get('admin_logged_in')):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    crowd_count = int(body.get('crowd_count', 0))
    wait_time   = float(body.get('wait_time', 0))
    crowd_level = body.get('crowd_level', _level_from_count(crowd_count))
    city        = body.get('city', 'Unknown')
    place_name  = body.get('place', 'Unknown')
    block_name  = body.get('block', 'Unknown')

    # Save manual update to database
    update = ManualUpdate.objects.create(
        city        = city,
        place       = place_name,
        block       = block_name,
        crowd_count = crowd_count,
        wait_time   = wait_time,
        crowd_level = crowd_level,
        submitted_by = request.user.username if request.user.is_authenticated else request.session.get('admin_username', 'admin'),
    )

    # Also save as a QueueSnapshot so chart history is preserved
    try:
        block_obj = QueueBlock.objects.get(
            place__name__icontains=place_name,
            name__icontains=block_name,
        )
        QueueSnapshot.objects.create(
            block          = block_obj,
            crowd_count    = crowd_count,
            wait_time_min  = wait_time,
            crowd_level    = crowd_level,
            source         = 'manual',
            raw_device_count = crowd_count,
        )
    except QueueBlock.DoesNotExist:
        logger.warning(f"QueueBlock not found for place='{place_name}' block='{block_name}'")

    logger.info(f"Manual update: {city}/{place_name}/{block_name} crowd={crowd_count} wait={wait_time} level={crowd_level}")

    return JsonResponse({
        'status':      'ok',
        'id':          update.pk,
        'crowd_count': crowd_count,
        'wait_time':   wait_time,
        'crowd_level': crowd_level,
        'timestamp':   update.submitted_at.isoformat(),
    })


@require_http_methods(['GET'])
def api_live_queue(request):
    """
    GET /api/live-queue?location=Cauvery+Hospital%2C+Halasuru&block=OP+Desk
    Returns current crowd data, respecting PRESENTATION_MODE.
    """
    location   = request.GET.get('location', '')
    block_name = request.GET.get('block', '')

    data = _get_live_data(location, block_name)

    # Append recent history for chart
    try:
        block = QueueBlock.objects.get(
            place__name__icontains=location,
            name__icontains=block_name,
        )
        since = timezone.now() - timedelta(hours=2)
        snapshots = block.snapshots.filter(
            timestamp__gte=since
        ).order_by('timestamp').values(
            'crowd_count', 'wait_time_min', 'timestamp', 'source'
        )
        data['history'] = [
            {
                'crowd_count': s['crowd_count'],
                'wait_time':   s['wait_time_min'],
                'timestamp':   s['timestamp'].isoformat(),
                'source':      s['source'],
            }
            for s in snapshots
        ]
    except QueueBlock.DoesNotExist:
        data['history'] = []

    return JsonResponse(data)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def api_presentation_mode(request):
    """
    GET  /api/presentation-mode
    POST /api/presentation-mode
    """
    if request.method == 'POST':
        if not (request.user.is_authenticated or request.session.get('admin_logged_in')):
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        try:
            body = json.loads(request.body)
            action = body.get('action', 'enable')
        except (json.JSONDecodeError, UnicodeDecodeError):
            action = request.GET.get('action', 'enable')

        config = PresentationConfig.get()
        if action == 'enable':
            config.is_enabled = True
            config.enabled_at = timezone.now()
            config.enabled_by = request.user.username if request.user.is_authenticated else request.session.get('admin_username', 'admin')
        else:
            config.is_enabled = False
        config.save()
        return JsonResponse({'presentation_mode': config.is_enabled, 'status': 'ok'})

    # GET
    config = PresentationConfig.get()
    return JsonResponse({
        'presentation_mode': config.is_enabled,
        'enabled_at':  config.enabled_at.isoformat() if config.enabled_at else None,
        'enabled_by':  config.enabled_by,
        'updated_at':  config.updated_at.isoformat(),
    })


@require_http_methods(['GET'])
def api_chart_history(request):
    """
    GET /api/chart-history?location=...&block=...&hours=2
    """
    location   = request.GET.get('location', '')
    block_name = request.GET.get('block', '')
    hours      = int(request.GET.get('hours', 2))

    try:
        block = QueueBlock.objects.get(
            place__name__icontains=location,
            name__icontains=block_name,
        )
        since = timezone.now() - timedelta(hours=hours)
        snapshots = block.snapshots.filter(
            timestamp__gte=since
        ).order_by('timestamp').values(
            'crowd_count', 'wait_time_min', 'timestamp', 'source'
        )
        history = [
            {
                'crowd_count': s['crowd_count'],
                'wait_time':   s['wait_time_min'],
                'timestamp':   s['timestamp'].isoformat(),
                'source':      s['source'],
                'ts_ms':       int(s['timestamp'].timestamp() * 1000),
            }
            for s in snapshots
        ]
        return JsonResponse({'history': history, 'count': len(history)})
    except QueueBlock.DoesNotExist:
        return JsonResponse({'history': [], 'count': 0})


@csrf_exempt
@require_http_methods(['POST'])
def api_calibrate(request):
    """
    POST /api/calibrate
    Supports dual format payload:
      - dashboard format: { "location": "...", "block": "...", "actual_count": ... }
      - ESP32 sniffer / curl format: { "zone_id": "...", "manual_count": ... }
    """
    if not (request.user.is_authenticated or request.session.get('admin_logged_in')):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    zone_id = body.get('zone_id')
    if zone_id:
        # Curl or ESP32 payload
        actual_count = int(body.get('manual_count', 1))
        try:
            block = QueueBlock.objects.get(zone_id=zone_id)
        except QueueBlock.DoesNotExist:
            return JsonResponse({'error': f'Zone {zone_id} not found'}, status=404)
    else:
        # Dashboard payload
        location     = body.get('location', '')
        block_name   = body.get('block', '')
        actual_count = int(body.get('actual_count', 1))
        try:
            block = QueueBlock.objects.get(
                place__name__icontains=location,
                name__icontains=block_name,
            )
        except QueueBlock.DoesNotExist:
            return JsonResponse({'error': 'Block not found'}, status=404)

    snapshot = block.snapshots.first()
    raw_count = snapshot.raw_device_count if snapshot else actual_count
    factor = actual_count / max(raw_count, 1)

    CalibrationRecord.objects.create(
        block              = block,
        calibration_factor = round(factor, 4),
        actual_count       = actual_count,
        raw_count          = raw_count,
        calibrated_by      = request.user.username if request.user.is_authenticated else request.session.get('admin_username', 'admin'),
    )
    return JsonResponse({
        'status': 'ok',
        'calibration_factor': round(factor, 4),
        'actual_count': actual_count,
        'raw_count': raw_count,
    })


# ─────────────────────────────────────────────────────────────
# MIGRATED FASTAPI SENSOR API ENDPOINTS
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def api_ingest(request):
    """
    POST /api/ingest
    Ingests raw counts from ESP32 sniffer or simulation.
    Payload: {"zone_id": "...", "count": ..., "rssi_threshold": ...}
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    zone_id = body.get('zone_id')
    count = int(body.get('count', 0))
    rssi_threshold = body.get('rssi_threshold')

    if not zone_id:
        return JsonResponse({'error': 'zone_id is required'}, status=400)

    # Resolve or dynamically create QueueBlock to prevent simulation crashes
    try:
        block = QueueBlock.objects.get(zone_id=zone_id)
    except QueueBlock.DoesNotExist:
        place, _ = Place.objects.get_or_create(
            city='Bangalore',
            name='Cauvery Hospital, Halasuru',
            defaults={'place_type': 'hospital'}
        )
        block = QueueBlock.objects.create(
            place=place,
            name=zone_id.replace('-', ' ').title(),
            zone_id=zone_id
        )

    # 1. Calibration factor (running average of CalibrationRecord)
    avg_factor = CalibrationRecord.objects.filter(block=block).aggregate(Avg('calibration_factor'))['calibration_factor__avg']
    factor = avg_factor if avg_factor is not None else 1.0

    # 2. EWMA Smoothing over the last 5 readings
    snapshots = block.snapshots.filter(source='sensor').order_by('-timestamp')[:(SMOOTHING_WINDOW - 1)]
    recent_raw = [s.raw_device_count for s in snapshots]
    recent_raw.reverse()
    recent_raw.append(count)

    smoothed = ewma(recent_raw)
    calibrated_count = smoothed * factor

    # 3. Estimated wait time (Little's Law)
    cfg = ZONE_CONFIG.get(zone_id, ZONE_CONFIG["default"])
    est_wait_sec = (calibrated_count / cfg["num_servers"]) * cfg["avg_service_time"]
    wait_time_min = round(est_wait_sec / 60.0, 1)

    # 4. Level classification
    if calibrated_count >= cfg["high_at"]:
        level = 'HIGH'
    elif calibrated_count >= cfg["medium_at"]:
        level = 'MEDIUM'
    else:
        level = 'LOW'

    # Save QueueSnapshot
    QueueSnapshot.objects.create(
        block=block,
        crowd_count=round(calibrated_count),
        wait_time_min=wait_time_min,
        crowd_level=level,
        source='sensor',
        raw_device_count=count,
        calibration_factor=factor
    )

    return JsonResponse({'status': 'ok'})


@require_http_methods(['GET'])
def api_zones(request):
    """
    GET /api/zones
    Returns a list of zones with latest readings, predictions, trends, and forecasts.
    """
    blocks = QueueBlock.objects.filter(is_active=True)
    zones_data = []

    for block in blocks:
        snapshot = block.snapshots.first()
        if not snapshot:
            continue

        # Trend calculation
        snapshots = block.snapshots.filter(source='sensor').order_by('-timestamp')[:5]
        recent_raw = [s.raw_device_count for s in snapshots]
        recent_raw.reverse()

        trend = "stable"
        if len(recent_raw) >= 2:
            smoothed = ewma(recent_raw)
            prev_smoothed = ewma(recent_raw[:-1])
            if prev_smoothed > 0:
                if smoothed > prev_smoothed * 1.1:
                    trend = "increasing"
                elif smoothed < prev_smoothed * 0.9:
                    trend = "decreasing"

        # Forecast calculation for the same hour of day
        hour_now = timezone.now().hour
        same_hour_snapshots = block.snapshots.filter(timestamp__hour=hour_now, source='sensor')
        avg_calibrated = same_hour_snapshots.aggregate(Avg('crowd_count'))['crowd_count__avg']
        forecast = round(avg_calibrated, 1) if avg_calibrated is not None else float(snapshot.crowd_count)

        zones_data.append({
            "zone_id": block.zone_id,
            "level": snapshot.crowd_level.lower(),
            "last_updated": snapshot.timestamp.timestamp(),
            "raw_count": snapshot.raw_device_count,
            "smoothed_count": float(snapshot.raw_device_count),
            "calibration_factor": snapshot.calibration_factor,
            "calibrated_count": float(snapshot.crowd_count),
            "estimated_wait_seconds": int(snapshot.wait_time_min * 60),
            "trend": trend,
            "forecast_count_this_hour": forecast
        })

    return JsonResponse({"zones": zones_data})


@require_http_methods(['GET'])
def api_history_by_zone(request, zone_id):
    """
    GET /api/history/{zone_id}
    """
    limit = int(request.GET.get('limit', 100))
    try:
        block = QueueBlock.objects.get(zone_id=zone_id)
    except QueueBlock.DoesNotExist:
        return JsonResponse({'error': 'Zone not found'}, status=404)

    snapshots = block.snapshots.filter(source='sensor').order_by('-timestamp')[:limit]
    points = [
        {"count": s.raw_device_count, "ts": s.timestamp.timestamp()}
        for s in reversed(snapshots)
    ]
    return JsonResponse({
        "zone_id": zone_id,
        "points": points
    })


@require_http_methods(['GET'])
def api_predict_by_zone(request, zone_id):
    """
    GET /api/predict/{zone_id}
    """
    try:
        block = QueueBlock.objects.get(zone_id=zone_id)
    except QueueBlock.DoesNotExist:
        return JsonResponse({'error': 'Zone not found'}, status=404)

    snapshot = block.snapshots.first()
    if not snapshot:
        return JsonResponse({'error': 'No data for this zone yet'}, status=404)

    snapshots = block.snapshots.filter(source='sensor').order_by('-timestamp')[:5]
    recent_raw = [s.raw_device_count for s in snapshots]
    recent_raw.reverse()

    trend = "stable"
    if len(recent_raw) >= 2:
        smoothed = ewma(recent_raw)
        prev_smoothed = ewma(recent_raw[:-1])
        if prev_smoothed > 0:
            if smoothed > prev_smoothed * 1.1:
                trend = "increasing"
            elif smoothed < prev_smoothed * 0.9:
                trend = "decreasing"

    hour_now = timezone.now().hour
    same_hour_snapshots = block.snapshots.filter(timestamp__hour=hour_now, source='sensor')
    avg_calibrated = same_hour_snapshots.aggregate(Avg('crowd_count'))['crowd_count__avg']
    forecast = round(avg_calibrated, 1) if avg_calibrated is not None else float(snapshot.crowd_count)

    return JsonResponse({
        "zone_id": zone_id,
        "level": snapshot.crowd_level.lower(),
        "last_updated": snapshot.timestamp.timestamp(),
        "raw_count": snapshot.raw_device_count,
        "smoothed_count": float(snapshot.raw_device_count),
        "calibration_factor": snapshot.calibration_factor,
        "calibrated_count": float(snapshot.crowd_count),
        "estimated_wait_seconds": int(snapshot.wait_time_min * 60),
        "trend": trend,
        "forecast_count_this_hour": forecast
    })


@require_http_methods(['GET'])
def api_health(request):
    """
    GET /api/health
    """
    import time
    return JsonResponse({"status": "ok", "time": time.time()})
