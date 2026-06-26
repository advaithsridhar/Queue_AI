# queueai/consumers.py
"""
WebSocket consumer for live queue data.

Clients connect to:
    ws://host:8000/ws/queue/<location>/<block>/

Example:
    ws://192.168.1.5:8000/ws/queue/Cauvery+Hospital/OP+Desk/

The consumer:
  1. Sends initial data on connect
  2. Pushes updates every 2 seconds (polling the DB)
  3. Receives broadcast group messages when admin posts a manual update
"""
import json
import asyncio
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class QueueConsumer(AsyncWebsocketConsumer):
    PUSH_INTERVAL = 2  # seconds

    # ── Lifecycle ──────────────────────────────────────────────────────
    async def connect(self):
        self.location = self.scope['url_route']['kwargs']['location']
        self.block    = self.scope['url_route']['kwargs']['block']

        # Channel group: one group per location+block
        self.group_name = (
            f"queue_{self.location}_{self.block}"
            .replace(' ', '_').replace(',', '').replace('.', '')
            .lower()
        )

        # Join group
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        logger.info(f"WS connected: {self.location}/{self.block} [{self.group_name}]")

        # Send initial state immediately
        data = await self.get_live_data()
        await self.send(text_data=json.dumps({'type': 'initial', **data}))

        # Start background push loop
        self._push_task = asyncio.ensure_future(self._periodic_push())

    async def disconnect(self, close_code):
        if hasattr(self, '_push_task'):
            self._push_task.cancel()
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info(f"WS disconnected: {self.location}/{self.block} (code={close_code})")

    async def receive(self, text_data):
        """
        Handle messages from the browser client.
        Currently unused but available for future bidirectional use.
        """
        try:
            msg = json.loads(text_data)
            msg_type = msg.get('type')
            if msg_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"WS receive error: {e}")

    # ── Group message handler (from admin push) ────────────────────────
    async def queue_update(self, event):
        """
        Called when admin POSTs a manual update.
        The signal/dispatch code in views.py calls:
            channel_layer.group_send(group_name, {'type': 'queue.update', 'data': {...}})
        """
        await self.send(text_data=json.dumps({
            'type': 'update',
            **event['data'],
        }))

    # ── Periodic push ──────────────────────────────────────────────────
    async def _periodic_push(self):
        while True:
            await asyncio.sleep(self.PUSH_INTERVAL)
            try:
                data = await self.get_live_data()
                await self.send(text_data=json.dumps({'type': 'update', **data}))
            except Exception as e:
                logger.warning(f"WS periodic push error: {e}")

    # ── Data fetch (runs sync DB query in thread pool) ─────────────────
    @database_sync_to_async
    def get_live_data(self):
        from .views import _get_live_data
        return _get_live_data(self.location, self.block)
