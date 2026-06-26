# QueueAI — Presentation Mode: Complete Setup Guide

## What's Added

| Feature | Details |
|---|---|
| Admin Panel | `/admin` — login with `admin` / `admin123` |
| Manual Update API | `POST /api/manual-update` |
| Live Queue API | `GET /api/live-queue` |
| Presentation Mode toggle | DB-backed, toggleable from admin panel |
| WebSocket live sync | Django Channels — no page refresh needed |
| Polling fallback | Every 2 seconds if WebSocket unavailable |
| Live chart updates | Chart.js appends new point on every admin push |
| Network access | `0.0.0.0:8000` — reachable from any device on same WiFi |

---

## 1. Install Dependencies

```bash
pip install django djangorestframework channels channels-redis daphne
```

For in-memory channel layer (no Redis, single-process dev):
```bash
# No extra install needed — channels.layers.InMemoryChannelLayer
```

For production with Redis:
```bash
pip install channels-redis
# Also: sudo apt install redis-server && redis-server
```

---

## 2. File Changes Checklist

### New files to create

| File | Purpose |
|---|---|
| `queueai/models.py` | DB models (Place, QueueBlock, QueueSnapshot, ManualUpdate, etc.) |
| `queueai/views.py` | All views + API endpoints |
| `queueai/consumers.py` | WebSocket consumer |
| `queueai/urls.py` | URL routing |
| `queueai/signals.py` | Broadcast WS on ManualUpdate save |
| `queueai/apps.py` | Register signals in `ready()` |

### Existing files to modify

#### `project/settings.py` — add:

```python
PRESENTATION_MODE = False  # default; DB toggle overrides this

INSTALLED_APPS = [
    # ... existing ...
    'channels',
    'queueai',
]

ASGI_APPLICATION = 'project.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
        # Production: switch to channels_redis.core.RedisChannelLayer
    }
}
```

#### `project/urls.py` — add:

```python
from django.urls import path, include

urlpatterns = [
    path('',            include('queueai.urls')),
    path('django-admin/', admin.site.urls),
]
```

#### `project/routing.py` — create:

```python
from django.urls import re_path
from queueai.consumers import QueueConsumer

websocket_urlpatterns = [
    re_path(
        r'ws/queue/(?P<location>[^/]+)/(?P<block>[^/]+)/$',
        QueueConsumer.as_asgi()
    ),
]
```

#### `project/asgi.py` — replace:

```python
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
```

---

## 3. Database Setup

```bash
python manage.py makemigrations queueai
python manage.py migrate

# Create Django superuser (for Django admin, optional)
python manage.py createsuperuser
```

### Seed initial data (run once):

```python
# Run via: python manage.py shell
from queueai.models import Place, QueueBlock

p1 = Place.objects.create(
    city='Bangalore',
    name='Cauvery Hospital, Halasuru',
    place_type='hospital'
)
QueueBlock.objects.create(place=p1, name='OP Desk',   zone_id='entrance-1')
QueueBlock.objects.create(place=p1, name='Lab',        zone_id='lab-zone')
QueueBlock.objects.create(place=p1, name='Pharmacy',   zone_id='pharma-zone')

p2 = Place.objects.create(
    city='Bangalore',
    name='Orion Mall, Yeshwanthpur',
    place_type='mall',
    is_active=False
)
QueueBlock.objects.create(place=p2, name='Food Court',       zone_id='zone-fc')
QueueBlock.objects.create(place=p2, name='Parking',          zone_id='zone-pk')
QueueBlock.objects.create(place=p2, name='Customer Service', zone_id='zone-cs')

p3 = Place.objects.create(
    city='Bangalore',
    name='HDFC Bank, Mahalakshmi layout',
    place_type='bank',
    is_active=False
)
QueueBlock.objects.create(place=p3, name='Teller',       zone_id='zone-t')
QueueBlock.objects.create(place=p3, name='Loan Counter', zone_id='zone-l')
QueueBlock.objects.create(place=p3, name='NRI Services', zone_id='zone-n')

print("Seed data created!")
```

---

## 4. Run the Server

### Development (single machine):

```bash
python manage.py runserver
# Access: http://127.0.0.1:8000/
```

### Network access (second laptop on same WiFi):

```bash
python manage.py runserver 0.0.0.0:8000
```

**Find your IP address:**

| OS | Command |
|---|---|
| Windows | `ipconfig` → IPv4 Address under Wi-Fi |
| macOS | `ifconfig en0 \| grep inet` |
| Linux | `ip addr show` or `hostname -I` |

**Second laptop access:**

```
http://192.168.x.x:8000/         ← Main QueueAI dashboard
http://192.168.x.x:8000/admin    ← Admin panel (login with admin/admin123)
```

Replace `192.168.x.x` with your actual IP from the command above.

### Production (with Daphne):

```bash
daphne -b 0.0.0.0 -p 8000 project.asgi:application
```

---

## 5. Presentation Mode — How It Works

```
PRESENTATION_MODE = False (default)
  └─ QueueSnapshot (sensor/ML data) is shown

PRESENTATION_MODE = True (toggled via admin panel)
  └─ ManualUpdate (admin-entered values) is shown
  └─ Sensor values are completely ignored
  └─ Blue banner shown on main dashboard
```

### Toggling via admin panel:
1. Open `http://192.168.x.x:8000/admin`
2. Login: `admin` / `admin123`
3. Click the **Presentation Mode** toggle in the top nav
4. The main dashboard immediately reflects the change

### Toggling via API:
```bash
# Enable
curl -X POST http://192.168.x.x:8000/api/presentation-mode \
  -H "Content-Type: application/json" \
  -d '{"action": "enable"}'

# Disable
curl -X POST http://192.168.x.x:8000/api/presentation-mode \
  -H "Content-Type: application/json" \
  -d '{"action": "disable"}'
```

---

## 6. API Reference

### POST `/api/manual-update`

Push a crowd update to the live dashboard.

**Request:**
```json
{
  "city":        "Bangalore",
  "place":       "Cauvery Hospital, Halasuru",
  "block":       "OP Desk",
  "crowd_count": 8,
  "wait_time":   3.5,
  "crowd_level": "MEDIUM"
}
```

**Response:**
```json
{
  "status":      "ok",
  "id":          42,
  "crowd_count": 8,
  "wait_time":   3.5,
  "crowd_level": "MEDIUM",
  "timestamp":   "2026-06-19T10:01:00+05:30"
}
```

---

### GET `/api/live-queue`

Get current data for a queue (respects Presentation Mode).

```
GET /api/live-queue?location=Cauvery+Hospital%2C+Halasuru&block=OP+Desk
```

**Response:**
```json
{
  "crowd_count":       8,
  "wait_time":         3.5,
  "crowd_level":       "MEDIUM",
  "source":            "manual",
  "presentation_mode": true,
  "timestamp":         "2026-06-19T10:01:00+05:30",
  "history": [
    { "crowd_count": 5, "wait_time": 2.0, "timestamp": "...", "source": "manual" },
    ...
  ]
}
```

---

### GET `/api/presentation-mode`

```json
{
  "presentation_mode": true,
  "enabled_at":  "2026-06-19T09:55:00+05:30",
  "enabled_by":  "admin",
  "updated_at":  "2026-06-19T09:55:00+05:30"
}
```

---

### WebSocket `ws://host:8000/ws/queue/<location>/<block>/`

Receives messages every 2 seconds (or immediately on admin push):

```json
{
  "type":            "update",
  "crowd_count":     8,
  "wait_time":       3.5,
  "crowd_level":     "MEDIUM",
  "source":          "manual",
  "presentation_mode": true,
  "timestamp":       "2026-06-19T10:01:05+05:30"
}
```

---

## 7. Live Chart Update — How It Works

When the admin pushes an update:

1. `ManualUpdate` is saved to DB
2. `post_save` signal fires → broadcasts to WebSocket group
3. All connected browsers receive the message instantly
4. JavaScript calls `addChartPoint(crowd, wait)` → Chart.js appends the new point
5. Chart re-renders with animation

Result: chart grows like:
```
10:01 PM → 5 people, 2.0 min wait
10:02 PM → 8 people, 3.5 min wait   ← admin push
10:03 PM → 12 people, 5.0 min wait  ← admin push
```

---

## 8. Template Integration (add to `queue.html`)

Add this at the bottom of your queue detail template, replacing any existing polling interval:

```html
<script>
// WebSocket live sync
(function() {
  const location = "{{ location }}";
  const block    = "{{ block }}";
  const wsScheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl    = `${wsScheme}://${window.location.host}/ws/queue/${encodeURIComponent(location)}/${encodeURIComponent(block)}/`;

  let ws, reconnectTimer;

  function connect() {
    ws = new WebSocket(wsUrl);

    ws.onopen  = () => document.getElementById('wsDot')?.classList.remove('disconnected');
    ws.onclose = () => {
      document.getElementById('wsDot')?.classList.add('disconnected');
      reconnectTimer = setTimeout(connect, 2000);  // auto-reconnect
    };
    ws.onmessage = (e) => {
      const d = JSON.parse(e.data);
      if (d.type === 'initial' || d.type === 'update') {
        applyLiveData(d);
        if (d.type === 'update') addChartPoint(d.crowd_count, d.wait_time);
      }
    };
  }

  // Fallback polling (if WebSocket not available)
  function startPolling() {
    setInterval(() => {
      fetch(`/api/live-queue?location=${encodeURIComponent(location)}&block=${encodeURIComponent(block)}`)
        .then(r => r.json()).then(d => { applyLiveData(d); addChartPoint(d.crowd_count, d.wait_time); });
    }, 2000);
  }

  try { connect(); } catch(e) { startPolling(); }
})();
</script>
```

---

## 9. Presentation Mode Demo Flow (for presentation day)

1. **Laptop A (host):** Run `python manage.py runserver 0.0.0.0:8000`
2. **Laptop A:** Find IP → `ipconfig` → e.g. `192.168.1.42`
3. **Laptop B (presenter):** Open `http://192.168.1.42:8000/` — shows live dashboard
4. **Laptop A (admin):** Open `http://192.168.1.42:8000/admin` → login
5. Enable **Presentation Mode** toggle in admin nav
6. Enter crowd values → click **Push Update to Live Dashboard**
7. **Laptop B** dashboard updates instantly — no refresh needed
8. Chart on Laptop B grows with each push

---

## 10. Existing Sensor Pipeline — No Changes Needed

The `PRESENTATION_MODE = False` path in `_get_live_data()` reads from `QueueSnapshot` exactly as before. Your ESP32 / WiFi probe sniffing code writes to `QueueSnapshot` → nothing changes in that flow.

Presentation Mode is purely an overlay:

```
Sensor ESP32 → QueueSnapshot table ─┐
                                     ├─ _get_live_data() ─→ API / WS
Admin Panel  → ManualUpdate table ──┘   (picks one based on flag)
```
