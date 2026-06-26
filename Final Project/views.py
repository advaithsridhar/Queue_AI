# queueai/views.py
import json
import logging
from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import (
    Place, QueueBlock, QueueSnapshot,
    ManualUpdate, PresentationConfig, CalibrationRecord
)

logger = logging.getLogger(__name__)

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
        'block': block_name,
        'live_data': live_data,
        'presentation_mode': live_data.get('presentation_mode', False),
    }
    return render(request, 'queueai/queue.html', context)


# ─────────────────────────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────────────────────────

def admin_login_view(request):
    """Hardcoded credentials admin login."""
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        # Hardcoded check (as required by spec)
        if username == 'admin' and password == 'admin123':
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
            # Even without a Django user, set a session flag
            request.session['admin_logged_in'] = True
            request.session['admin_username'] = username
            return redirect('queueai:admin_dashboard')
        return render(request, 'queueai/admin_login.html', {'error': 'Invalid credentials'})
    return render(request, 'queueai/admin_login.html')


def admin_required(view_func):
    """Custom decorator checking session-based admin login."""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('admin_logged_in'):
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
# API — MANUAL UPDATE
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def api_manual_update(request):
    """
    POST /api/manual-update
    Body (JSON):
    {
        "city":        "Bangalore",
        "place":       "Cauvery Hospital, Halasuru",
        "block":       "OP Desk",
        "crowd_count": 8,
        "wait_time":   3.5,
        "crowd_level": "MEDIUM"
    }
    """
    if not request.session.get('admin_logged_in'):
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

    # Save to database
    update = ManualUpdate.objects.create(
        city        = city,
        place       = place_name,
        block       = block_name,
        crowd_count = crowd_count,
        wait_time   = wait_time,
        crowd_level = crowd_level,
        submitted_by = request.session.get('admin_username', 'admin'),
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


# ─────────────────────────────────────────────────────────────
# API — LIVE QUEUE
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# API — PRESENTATION MODE
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def api_presentation_mode(request):
    """
    GET  /api/presentation-mode          → returns current status
    POST /api/presentation-mode          → body: {"action": "enable"|"disable"}
    """
    if request.method == 'POST':
        if not request.session.get('admin_logged_in'):
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
            config.enabled_by = request.session.get('admin_username', 'admin')
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


# ─────────────────────────────────────────────────────────────
# API — CHART HISTORY
# ─────────────────────────────────────────────────────────────

@require_http_methods(['GET'])
def api_chart_history(request):
    """
    GET /api/chart-history?location=...&block=...&hours=2
    Returns time-series data for the queue trend chart.
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


# ─────────────────────────────────────────────────────────────
# API — CALIBRATION
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def api_calibrate(request):
    """
    POST /api/calibrate
    Body: { "location": "...", "block": "...", "actual_count": 8 }
    """
    if not request.session.get('admin_logged_in'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    location     = body.get('location', '')
    block_name   = body.get('block', '')
    actual_count = int(body.get('actual_count', 1))

    try:
        block = QueueBlock.objects.get(
            place__name__icontains=location,
            name__icontains=block_name,
        )
        snapshot = block.snapshots.first()
        raw_count = snapshot.raw_device_count if snapshot else actual_count
        factor = actual_count / max(raw_count, 1)

        CalibrationRecord.objects.create(
            block              = block,
            calibration_factor = round(factor, 4),
            actual_count       = actual_count,
            raw_count          = raw_count,
            calibrated_by      = request.session.get('admin_username', 'admin'),
        )
        return JsonResponse({
            'status': 'ok',
            'calibration_factor': round(factor, 4),
            'actual_count': actual_count,
            'raw_count': raw_count,
        })
    except QueueBlock.DoesNotExist:
        return JsonResponse({'error': 'Block not found'}, status=404)


# ─────────────────────────────────────────────────────────────
# WEBSOCKET CONSUMER (Django Channels)
# ─────────────────────────────────────────────────────────────
# Put this in queueai/consumers.py

CONSUMER_CODE = '''
# queueai/consumers.py
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class QueueConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for live queue data.
    Clients connect to: ws://host:8000/ws/queue/<location>/<block>/
    """

    async def connect(self):
        self.location = self.scope['url_route']['kwargs']['location']
        self.block    = self.scope['url_route']['kwargs']['block']
        self.group    = f"queue_{self.location}_{self.block}".replace(' ', '_')

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

        # Send initial data on connect
        data = await self.get_live_data()
        await self.send(text_data=json.dumps({'type': 'initial', **data}))

        # Start periodic push every 2 seconds
        self.push_task = asyncio.ensure_future(self.periodic_push())

    async def disconnect(self, close_code):
        if hasattr(self, 'push_task'):
            self.push_task.cancel()
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data):
        """Handle messages from client (e.g. calibration)."""
        pass

    async def queue_update(self, event):
        """Handle group messages (from admin push)."""
        await self.send(text_data=json.dumps(event['data']))

    async def periodic_push(self):
        while True:
            await asyncio.sleep(2)
            data = await self.get_live_data()
            await self.send(text_data=json.dumps({'type': 'update', **data}))

    @database_sync_to_async
    def get_live_data(self):
        from .views import _get_live_data
        return _get_live_data(self.location, self.block)
'''
