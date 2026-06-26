from django.apps import AppConfig


class QueueaiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'queueai'

    def ready(self):
        import queueai.signals  # noqa: F401
