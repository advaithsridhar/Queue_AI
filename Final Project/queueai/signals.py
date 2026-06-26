from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import PresentationConfig


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
        logging.getLogger(__name__).warning(f"WS manual broadcast failed: {e}")


@receiver(post_save, sender='queueai.QueueSnapshot')
def broadcast_queue_snapshot(sender, instance, created, **kwargs):
    if not created:
        return
    
    # Bypass broadcasting sensor updates if Presentation Mode is active
    config = PresentationConfig.get()
    if config.is_enabled:
        return

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        group_name = _get_group_name(instance.block.place.name, instance.block.name)

        payload = {
            'type':        'queue.update',  # maps to QueueConsumer.queue_update()
            'data': {
                'crowd_count':       instance.crowd_count,
                'wait_time':         instance.wait_time_min,
                'crowd_level':       instance.crowd_level,
                'source':            instance.source,
                'presentation_mode': False,
                'timestamp':         instance.timestamp.isoformat(),
            }
        }
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"WS sensor broadcast failed: {e}")
