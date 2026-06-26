from django.urls import re_path
from queueai.consumers import QueueConsumer

websocket_urlpatterns = [
    re_path(
        r'ws/queue/(?P<location>[^/]+)/(?P<block>[^/]+)/$',
        QueueConsumer.as_asgi()
    ),
]
