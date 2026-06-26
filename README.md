# Smart Queue Prediction & Crowd Monitoring (ESP32 WiFi Probe Sensor)

A starter system that turns one or more ESP32 boards into passive
"people counters" by listening for WiFi probe requests — the broadcast
packets nearly every phone sends out while searching for networks —
and uses the resulting device counts to estimate crowd levels and
queue wait times.

## How it works

```
 ┌──────────────┐  WiFi Probe Requests   ┌──────────────────┐
 │  Phones in    │ ─────────────────────▶│   ESP32 sniffer   │
 │  the area     │   (broadcast, no       │  (promiscuous     │
 │               │    connection needed)  │   mode + channel  │
 └──────────────┘                         │   hopping +       │
                                           │   presence-window │
                                           │   counting)       │
                                           └─────────┬─────────┘
                                                      │ every 30s:
                                                      │ POST {zone_id, count}
                                                      ▼
                                           ┌──────────────────┐
                                           │  Backend (FastAPI │
                                           │  + SQLite)        │
                                           │  - stores history │
                                           │  - smooths counts │
                                           │    (EWMA)         │
                                           │  - applies        │
                                           │    calibration    │
                                           │  - Little's Law   │
                                           │    wait estimate  │
                                           │  - trend/forecast │
                                           └─────────┬─────────┘
                                                      │ GET /api/zones
                                                      ▼
                                           ┌──────────────────┐
                                           │  Dashboard        │
                                           │  (index.html)     │
                                           │  live cards +     │
                                           │  history chart +  │
                                           │  calibration form │
                                           └──────────────────┘
```

The ESP32 **does not need to join your WiFi network to count devices** —
it only connects briefly every 30 seconds to send its count to the
backend. Counting itself happens in promiscuous mode, which works
regardless of whether nearby phones are connected to anything.

## What's in this project

```
smart-queue/
├── firmware/probe_sniffer/probe_sniffer.ino   ESP32 Arduino sketch
├── backend/server.py                          FastAPI + SQLite API
├── backend/requirements.txt
└── dashboard/index.html                       Live dashboard (open in any browser)
```

## 1. Hardware needed

- 1+ ESP32 dev boards (any WROOM-32 / DevKitC / NodeMCU-32S works) —
  one per "zone" you want to monitor (entrance, checkout line, gate, etc.)
- USB cable + 5V power source for each board
- A WiFi network the ESP32 can briefly join to reach your backend
  (it does not need internet access, just to reach the backend's IP)

## 2. Flash the ESP32 firmware

1. Open `firmware/probe_sniffer/probe_sniffer.ino` in the Arduino IDE
   (install the **esp32 board package** by Espressif if you haven't).
2. Edit the config block at the top:
   - `WIFI_SSID` / `WIFI_PASSWORD` — used only for the brief reporting connection
   - `SERVER_URL` — `http://<backend-ip>:8000/api/ingest`
   - `ZONE_ID` — a unique name for this sensor's location, e.g. `"entrance-1"`
   - `RSSI_THRESHOLD` — tune this to roughly match your area's size
     (closer to 0 = smaller detection radius). Walk to the edge of the
     area while watching Serial output to pick a sensible value.
   - `PRESENCE_WINDOW_MS` — how long a device is still counted as
     "present" after its last probe (default 2 minutes). Shorten for
     a fast-moving line, lengthen for a waiting room.
   - `MIN_HITS` — how many probes a device needs before it's counted
     (default 2). This filters out single "drive-by" detections from
     people just walking past.
3. Select your board + port, then Upload.
4. Open Serial Monitor (115200 baud) — you should see counts and
   `HTTP 200` reports every 30 seconds.

## 3. Run the backend

```bash
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn server:app --host 0.0.0.0 --port 8000
```

Before going live, open `server.py` and add an entry to `ZONE_CONFIG`
for each `ZONE_ID` you flashed, with realistic values:

```python
ZONE_CONFIG = {
    "entrance-1": {"avg_service_time": 45, "num_servers": 2, "medium_at": 8, "high_at": 20},
}
```

- `avg_service_time` — how many seconds it takes staff to serve one
  person/customer at that zone
- `num_servers` — how many people/counters are serving simultaneously
- `medium_at` / `high_at` — device-count thresholds for the
  Low / Medium / High badges

## 4. Open the dashboard

Just open `dashboard/index.html` in a browser (no build step needed).
Set the backend URL at the top right (default `http://localhost:8000`)
and click **Connect**. It auto-refreshes every 15 seconds.

## Calibrating accuracy (important)

WiFi probe counting gives you a **relative** crowd signal, not an
exact head count, mainly because:

- **MAC randomization**: since Android 9 / iOS 14, phones use a
  randomized "private" MAC address for probe requests, often a
  *different* one each time WiFi scanning restarts. This tends to
  make raw counts **higher** than the true number of people. The v2
  firmware's presence-window counting (see above) reduces this, but
  doesn't eliminate it.
- **Range/walls**: RSSI doesn't map perfectly to distance, especially
  indoors.

The backend already smooths out reading-to-reading noise (see
`SMOOTHING_WINDOW`/`SMOOTHING_ALPHA` in `server.py`). To close the
remaining gap between the device count and a real headcount, use the
built-in **calibration** workflow:

1. Stand in a zone and count the actual number of people there.
2. On the dashboard, type that number into the "Actual headcount"
   field on that zone's card and click **Calibrate**.
3. The backend records `manual_count / raw_count` and maintains a
   running average as `calibration_factor`, which is applied to all
   future readings (`calibrated_count = smoothed_count × calibration_factor`).
4. Repeat a few times across light, medium, and busy periods for a
   more representative factor. You can also call this directly:

   ```bash
   curl -X POST http://<backend-ip>:8000/api/calibrate \
     -H "Content-Type: application/json" \
     -d '{"zone_id": "entrance-1", "manual_count": 6}'
   ```

The Low/Medium/High thresholds (`medium_at`/`high_at` in
`ZONE_CONFIG`) and the wait-time estimate are based on
`calibrated_count`, so calibrating directly improves both.

Even without calibration, the **trend** (rising/falling) is already
based on smoothed values and is usually reliable enough to drive
queue alerts and staffing decisions.

## Privacy & legal notes

This system only ever transmits a **count** from the ESP32 to the
backend — raw MAC addresses never leave the device in this firmware.
Still, passively scanning for nearby devices may be regulated where
you deploy it:

- Check local privacy law (e.g., GDPR in the EU/UK) — passive WiFi
  scanning for occupancy counting is generally treated similarly to a
  security camera footfall counter, but requirements vary by region.
- Consider posting a small sign noting that WiFi-based occupancy
  counting is in use.
- Don't extend this system to log or store individual MAC addresses
  without a clear legal basis and a data-retention policy.

## Extending this system

- **Multiple zones**: flash more ESP32s with different `ZONE_ID`s —
  the backend and dashboard already handle any number of zones.
- **Better channel coverage**: add a second "dedicated sniffer" ESP32
  that never joins WiFi and instead relays counts via **ESP-NOW** to a
  gateway ESP32 that has the internet connection — this lets the
  sniffer hop channels continuously without the connect/disconnect cycle.
- **Alerts**: add a check in `server.py` that sends a notification
  (e.g., via a webhook to Slack/Telegram) when a zone's `level`
  becomes `"high"`.
- **Better forecasting**: replace the hour-of-day average in
  `predict_for()` with a proper time-series model (e.g., Prophet,
  or an exponential smoothing library) once you have a few weeks of data.
- **Even better accuracy**: if you have multiple ESP32s covering the
  same physical area from different angles, average their reported
  counts on the backend - this reduces the impact of any single
  sensor's blind spots or RSSI quirks.
- **Display**: drive a small e-ink or OLED screen directly from a
  zone's ESP32 to show "Wait: ~4 min" at the queue entrance itself.
