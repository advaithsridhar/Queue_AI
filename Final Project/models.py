# queueai/models.py
from django.db import models
from django.utils import timezone


class Place(models.Model):
    """A physical location that has queues (hospital, mall, bank, etc.)"""
    PLACE_TYPES = [
        ('hospital', 'Hospital'),
        ('mall', 'Mall'),
        ('bank', 'Bank'),
        ('government', 'Government Office'),
        ('other', 'Other'),
    ]
    city        = models.CharField(max_length=100)
    name        = models.CharField(max_length=200)
    place_type  = models.CharField(max_length=50, choices=PLACE_TYPES, default='other')
    address     = models.TextField(blank=True)
    latitude    = models.FloatField(null=True, blank=True)
    longitude   = models.FloatField(null=True, blank=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('city', 'name')

    def __str__(self):
        return f"{self.name} ({self.city})"


class QueueBlock(models.Model):
    """A specific queue/counter inside a place (e.g. 'OP Desk', 'Pharmacy')"""
    place       = models.ForeignKey(Place, on_delete=models.CASCADE, related_name='blocks')
    name        = models.CharField(max_length=100)
    zone_id     = models.CharField(max_length=100, default='entrance-1')
    is_active   = models.BooleanField(default=True)

    class Meta:
        unique_together = ('place', 'name')

    def __str__(self):
        return f"{self.place.name} — {self.name}"


class QueueSnapshot(models.Model):
    """Time-series record of crowd/wait data for a queue block"""
    LEVEL_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
    ]
    SOURCE_CHOICES = [
        ('sensor', 'WiFi Sensor'),
        ('manual', 'Admin Manual'),
        ('ml', 'ML Prediction'),
    ]
    block           = models.ForeignKey(QueueBlock, on_delete=models.CASCADE, related_name='snapshots')
    crowd_count     = models.IntegerField(default=0)
    wait_time_min   = models.FloatField(default=0.0, help_text='Wait time in minutes')
    crowd_level     = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='LOW')
    source          = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='sensor')
    raw_device_count = models.IntegerField(default=0, help_text='Raw WiFi probe count before calibration')
    calibration_factor = models.FloatField(default=1.0)
    timestamp       = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        indexes  = [models.Index(fields=['block', '-timestamp'])]

    def __str__(self):
        return f"{self.block} @ {self.timestamp:%Y-%m-%d %H:%M:%S} → crowd={self.crowd_count}"


class ManualUpdate(models.Model):
    """Admin-submitted manual crowd update (Presentation Mode data)"""
    LEVEL_CHOICES = [('LOW','Low'),('MEDIUM','Medium'),('HIGH','High')]
    city        = models.CharField(max_length=100)
    place       = models.CharField(max_length=200)
    block       = models.CharField(max_length=100)
    crowd_count = models.IntegerField()
    wait_time   = models.FloatField()
    crowd_level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    submitted_at = models.DateTimeField(auto_now_add=True)
    submitted_by = models.CharField(max_length=100, default='admin')

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"[{self.submitted_at:%H:%M:%S}] {self.place}/{self.block} crowd={self.crowd_count}"


class PresentationConfig(models.Model):
    """Singleton config for presentation mode toggle"""
    is_enabled  = models.BooleanField(default=False)
    enabled_at  = models.DateTimeField(null=True, blank=True)
    enabled_by  = models.CharField(max_length=100, blank=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Presentation Config'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"Presentation Mode: {'ON' if self.is_enabled else 'OFF'}"


class CalibrationRecord(models.Model):
    """Calibration factor history per zone"""
    block           = models.ForeignKey(QueueBlock, on_delete=models.CASCADE, related_name='calibrations')
    calibration_factor = models.FloatField()
    actual_count    = models.IntegerField()
    raw_count       = models.IntegerField()
    calibrated_at   = models.DateTimeField(auto_now_add=True)
    calibrated_by   = models.CharField(max_length=100, default='admin')

    class Meta:
        ordering = ['-calibrated_at']

    def __str__(self):
        return f"{self.block} factor={self.calibration_factor} @ {self.calibrated_at:%H:%M}"
