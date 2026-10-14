from datetime import datetime, timedelta
from django.db import models
from agencies.models import Agency

class City(models.Model):
    name = models.CharField(max_length=100, unique=True)
    region = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name

class Line(models.Model):
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='lines')
    name = models.CharField(max_length=100, help_text="Ex: Littoral - Centre VIP")
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=5000, verbose_name="Prix du trajet complet (FCFA)")
    
    def __str__(self):
        return f"{self.name} ({self.agency.name})"

    @property
    def departure_city(self):
        first_stop = self.stops.order_by('stop_order').first()
        return first_stop.city if first_stop else None

    @property
    def arrival_city(self):
        last_stop = self.stops.order_by('-stop_order').first()
        return last_stop.city if last_stop else None

    def get_duration(self):
        """Retourne la durée estimée du trajet selon le dernier arrêt de la ligne (défaut: 4h)"""
        last_stop = self.stops.order_by('-stop_order').first()
        if last_stop and last_stop.duration_from_start:
            if isinstance(last_stop.duration_from_start, timedelta) and last_stop.duration_from_start.total_seconds() > 0:
                return last_stop.duration_from_start
        return timedelta(hours=4)

class LineStop(models.Model):
    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='stops')
    city = models.ForeignKey(City, on_delete=models.CASCADE)
    stop_order = models.PositiveIntegerField(help_text="Ordre de l'arrêt (1 pour le départ, 2 pour le premier arrêt, etc.)")
    duration_from_start = models.DurationField(help_text="Durée depuis le départ (00:00:00 pour le point de départ)", default=timedelta(0))
    price_from_start = models.DecimalField(max_digits=10, decimal_places=2, help_text="Prix cumulé depuis le départ (0 pour le point de départ)", default=0)

    class Meta:
        ordering = ['stop_order']
        unique_together = ('line', 'stop_order')

    def __str__(self):
        return f"{self.city.name} (Arrêt {self.stop_order})"

class Vehicle(models.Model):
    OPERATIONAL_STATUS_CHOICES = (
        ('available', 'Disponible'),
        ('in_trip', 'En voyage'),
        ('maintenance', 'En maintenance'),
        ('out_of_service', 'Hors service'),
    )
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='vehicles')
    vehicle_type = models.CharField(max_length=50) # ex: Bus VIP, Coaster
    capacity = models.PositiveIntegerField()
    amenities = models.TextField(blank=True, help_text="Climatisation, Wi-Fi, etc.")
    registration = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=20,
        choices=OPERATIONAL_STATUS_CHOICES,
        default='available',
        verbose_name="Disponibilité opérationnelle"
    )
    
    def __str__(self):
        return f"{self.vehicle_type} ({self.capacity} places) - {self.registration or self.agency.name}"

class Driver(models.Model):
    """Conducteur / Chauffeur professionnel affecté aux départs de l'agence"""
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='drivers')
    first_name = models.CharField(max_length=100, verbose_name="Prénom")
    last_name = models.CharField(max_length=100, verbose_name="Nom de famille")
    phone = models.CharField(max_length=30, verbose_name="Téléphone mobile")
    license_number = models.CharField(max_length=50, blank=True, verbose_name="N° Permis de conduire (Catégorie D)")
    is_active = models.BooleanField(default=True, verbose_name="Actif / En service")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = "Chauffeur"
        verbose_name_plural = "Chauffeurs"

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.phone})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

class Departure(models.Model):
    STATUS_CHOICES = (
        ('scheduled', 'Programmé'),
        ('boarding', 'Embarquement'),
        ('departed', 'Parti / Clôturé'),
        ('cancelled', 'Annulé'),
    )
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='departures')
    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='departures')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name='departures', verbose_name="Chauffeur assigné")
    date = models.DateField()
    time = models.TimeField()
    available_capacity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    
    def __str__(self):
        return f"{self.line.name} - {self.date} {self.time} ({self.agency.name})"

    def get_trip_start_datetime(self):
        return datetime.combine(self.date, self.time)

    def get_trip_end_datetime(self):
        return self.get_trip_start_datetime() + self.line.get_duration()

    def calculate_pricing(self, start_stop=None, end_stop=None):
        """
        Calcule de manière centralisée et uniforme :
        - agency_price : tarif brut revenant à l'agence (ex: 5 000 FCFA)
        - platform_fee : commission / frais EasyTravel (ex: 3% -> 150 FCFA)
        - total_price  : tarif unitaire final payé par le voyageur (ex: 5 150 FCFA)
        """
        raw_price = 0
        if start_stop and end_stop:
            if hasattr(end_stop, 'price_from_start') and hasattr(start_stop, 'price_from_start'):
                diff = float(end_stop.price_from_start) - float(start_stop.price_from_start)
                if diff > 0:
                    raw_price = diff
            if raw_price <= 0 and hasattr(end_stop, 'price_from_start') and float(end_stop.price_from_start) > 0:
                raw_price = float(end_stop.price_from_start)
        
        if raw_price <= 0:
            raw_price = float(self.line.base_price) if (self.line and self.line.base_price > 0) else 5000.0
            
        agency_price = float(raw_price)
        
        # Taux de commission de l'agence (défaut 3%)
        fee_pct = float(self.agency.platform_fee_percentage) / 100.0 if (self.agency and hasattr(self.agency, 'platform_fee_percentage')) else 0.03
        platform_fee = agency_price * fee_pct
        total_price = agency_price + platform_fee
        
        return {
            'agency_price': agency_price,
            'platform_fee': platform_fee,
            'total_price': total_price,
        }

    @property
    def start_city(self):
        return self.line.departure_city

    @property
    def end_city(self):
        return self.line.arrival_city

    @property
    def passenger_count(self):
        valid = self.bookings.exclude(status='cancelled')
        return sum(b.seats_reserved for b in valid)

    @property
    def booked_seats(self):
        return self.passenger_count

    @property
    def total_capacity(self):
        if self.vehicle and self.vehicle.capacity:
            return self.vehicle.capacity
        return (self.available_capacity or 0) + self.booked_seats or 50

    @property
    def occupancy_rate(self):
        if not self.total_capacity:
            return 0
        rate = (self.booked_seats / self.total_capacity) * 100
        return min(100.0, max(0.0, round(rate, 1)))

    @property
    def has_active_bookings(self):
        return self.bookings.exclude(status='cancelled').exists()

    @property
    def can_be_deleted(self):
        return self.bookings.count() == 0

    def is_closed_for_sale(self):
        from django.utils import timezone
        now = timezone.localtime()
        if self.status in ['departed', 'cancelled']:
            return True
        if self.date < now.date():
            return True
        if self.date == now.date() and self.time <= now.time():
            return True
        if self.available_capacity <= 0:
            return True
        return False
