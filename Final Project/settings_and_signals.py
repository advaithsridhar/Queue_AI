# ══════════════════════════════════════════════════════════════════════════
# SETTINGS ADDITIONS — add these to your existing settings.py
# ══════════════════════════════════════════════════════════════════════════

# ─── Presentation Mode ────────────────────────────────────────────────────
# Set True to bypass ML/sensor pipeline and use admin-entered values only.
# Can also be toggled at runtime via the admin panel (stored in DB).
PRESENTATION_MODE = False  # default off; admin panel DB setting takes precedence

# ─── Django Channels (WebSocket) ─────────────────────────────────────────
INSTALLED_APPS = [
    # ... your existing apps ...
    'channels',
    'queueai',
]

# Use channels as the ASGI application
ASGI_APPLICATION = 'project.asgi.application'

# Channel layer — use Redis in production, in-memory for dev/demo
CHANNEL_LAYERS = {
    'default': {
        # In-memory (single process, dev only):
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
        # For production with Redis:
        # 'BACKEND': 'channels_redis.core.RedisChannelLayer',
        # 'CONFIG': {'hosts': [('127.0.0.1', 6379)]},
    }
}

# ─── Session (for admin login) ────────────────────────────────────────────
SESSION_ENGINE     = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400  # 24 hours

# ─── CORS (if frontend on different port) ────────────────────────────────
# pip install django-cors-headers
# INSTALLED_APPS += ['corsheaders']
# MIDDLEWARE = ['corsheaders.middleware.CorsMiddleware', ...rest...]
# CORS_ALLOW_ALL_ORIGINS = True  # dev only


# ══════════════════════════════════════════════════════════════════════════
# queueai/signals.py — Broadcast WebSocket update when admin saves ManualUpdate
# ══════════════════════════════════════════════════════════════════════════

# queueai/signals.py
"""
After a ManualUpdate is saved, broadcast the new values to all connected
WebSocket clients watching that location/block.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver


def _get_group_name(place: str, block: str) -> str:
    return (
        f"queue_{place}_{block}"
        .replace(' ', '_').replace(',', '').replace('.', '')
        .lower()
    )


@receiver(post_save, sender='queueai.ManualUpdate')
def broadcast_manual_update(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        group_name = _get_group_name(instance.place, instance.block)

        payload = {
            'type':        'queue.update',  # maps to QueueConsumer.queue_update()
            'data': {
                'crowd_count':       instance.crowd_count,
                'wait_time':         instance.wait_time,
                'crowd_level':       instance.crowd_level,
                'source':            'manual',
                'presentation_mode': True,
                'timestamp':         instance.submitted_at.isoformat(),
            }
        }
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"WS broadcast failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# queueai/apps.py — Wire up signals
# ══════════════════════════════════════════════════════════════════════════

# queueai/apps.py
from django.apps import AppConfig


class QueueaiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'queueai'

    def ready(self):
        import queueai.signals  # noqa: F401


# ══════════════════════════════════════════════════════════════════════════
# JavaScript WebSocket client code — add to your queue detail template
# ══════════════════════════════════════════════════════════════════════════

JS_WEBSOCKET_CLIENT = """
// In your queue.html template, replace the polling setInterval with this:

(function() {
  const location  = '{{ location }}';   // Django template variable
  const block     = '{{ block }}';      // Django template variable
  const wsScheme  = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl     = `${wsScheme}://${window.location.host}/ws/queue/${encodeURIComponent(location)}/${encodeURIComponent(block)}/`;

  let ws;
  let reconnectTimer;

  function connect() {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('[QueueAI WS] Connected');
      document.getElementById('wsDot').classList.remove('disconnected');
      clearTimeout(reconnectTimer);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'initial' || data.type === 'update') {
        applyLiveData(data);        // your existing function
        if (data.type === 'update') {
          addChartPoint(data.crowd_count, data.wait_time);  // live chart update
        }
      }
    };

    ws.onerror = (err) => {
      console.warn('[QueueAI WS] Error', err);
    };

    ws.onclose = () => {
      console.log('[QueueAI WS] Disconnected, reconnecting in 2s...');
      document.getElementById('wsDot').classList.add('disconnected');
      reconnectTimer = setTimeout(connect, 2000);
    };
  }

  // Fallback polling if WebSocket is unavailable
  function startPolling() {
    setInterval(() => {
      fetch(`/api/live-queue?location=${encodeURIComponent(location)}&block=${encodeURIComponent(block)}`)
        .then(r => r.json())
        .then(data => {
          applyLiveData(data);
          addChartPoint(data.crowd_count, data.wait_time);
        })
        .catch(console.warn);
    }, 2000);
  }

  try {
    connect();
  } catch(e) {
    console.warn('[QueueAI WS] WebSocket not available, falling back to polling');
    startPolling();
  }
})();
"""
