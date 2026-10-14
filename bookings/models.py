import uuid
from django.db import models
from django.conf import settings
from travel.models import Departure, LineStop

class BookingCart(models.Model):
    reference = models.CharField(max_length=15, unique=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='carts')
    created_at = models.DateTimeField(auto_now_add=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, default='pending')
    transaction_id = models.CharField(max_length=100, blank=True, default="")
    
    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"CRT-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

class Booking(models.Model):
    STATUS_CHOICES = (
        ('pending', 'En attente'),
        ('confirmed', 'Confirmée'),
        ('boarded', 'Embarqué(e)'),
        ('cancelled', 'Annulée'),
    )
    ID_TYPE_CHOICES = (
        ('cni', "Carte Nationale d'Identité (CNI)"),
        ('passport', "Passeport"),
        ('school_card', "Carte scolaire"),
        ('other', "Autre"),
        ('none', "Aucune pièce"),
    )
    
    PAYMENT_STATUS_CHOICES = (
        ('paid', 'Payé / Encaissé'),
        ('pending', 'En attente de règlement'),
        ('refunded', 'Remboursé'),
        ('failed', 'Échoué / Non réglé'),
    )
    PAYMENT_METHOD_CHOICES = (
        ('cash', 'Espèces (Guichet)'),
        ('orange_money', 'Orange Money'),
        ('mtn_momo', 'MTN Mobile Money'),
        ('card', 'Carte Bancaire'),
        ('online', 'Paiement en ligne'),
        ('wallet', 'Portefeuille EasyTravel'),
    )
    
    cart = models.ForeignKey(BookingCart, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    reference = models.CharField(max_length=15, unique=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name='bookings')
    
    # Tronçon réservé
    departure_stop = models.ForeignKey(LineStop, on_delete=models.CASCADE, related_name='departing_bookings', null=True, blank=True)
    arrival_stop = models.ForeignKey(LineStop, on_delete=models.CASCADE, related_name='arriving_bookings', null=True, blank=True)
    
    # Infos Voyageur & Pièce d'identité
    traveler_name = models.CharField(max_length=150, verbose_name="Nom complet du voyageur")
    traveler_phone = models.CharField(max_length=25, verbose_name="Numéro de téléphone camerounais")
    traveler_email = models.EmailField(blank=True, null=True, verbose_name="Adresse email")
    id_type = models.CharField(max_length=20, choices=ID_TYPE_CHOICES, default='none', verbose_name="Type de pièce")
    id_number = models.CharField(max_length=50, default="", blank=True, null=True, verbose_name="Numéro de la pièce d'identité")
    companions = models.JSONField(default=list, blank=True, null=True, verbose_name="Accompagnants")
    
    # Détails Réservation & Encaissement
    seats_reserved = models.PositiveIntegerField(default=1)
    
    # Séparation Financière
    agency_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Montant reversé à l'agence")
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Frais de service EasyTravel")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Montant total payé par le client (Agence + Frais)")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending', verbose_name="Statut du paiement")
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, blank=True, null=True, verbose_name="Mode d'encaissement")
    transaction_id = models.CharField(max_length=100, blank=True, default="", verbose_name="ID Transaction Passerelle")
    payment_operator = models.CharField(max_length=50, blank=True, default="", verbose_name="Opérateur (MTN, Orange, etc.)")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Date d'encaissement")
    created_at = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        if not self.reference:
            # Génération d'une référence courte, pro et unique: ET-XXXXXXXX
            self.reference = f"ET-{str(uuid.uuid4()).upper()[:8]}"
        if self.payment_status == 'paid' and not self.paid_at:
            from django.utils import timezone
            self.paid_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Réservation {self.reference} - {self.traveler_name}"

    @property
    def unit_price(self):
        """Tarif unitaire TTC payé par passager"""
        if self.seats_reserved and self.seats_reserved > 0:
            return self.total_amount / self.seats_reserved
        return self.total_amount

    def get_security_signature(self):
        """
        Génère une signature cryptographique HMAC-SHA256 unique et inviolable pour le billet.
        Basée sur les identifiants clés et immuables de la réservation.
        Utilise TICKET_SIGNING_KEY (découplée de SECRET_KEY).
        """
        import hmac
        import hashlib
        ticket_key = getattr(settings, 'TICKET_SIGNING_KEY', settings.SECRET_KEY).encode()
        payload = f"EASYTRAVEL:{self.reference}:{self.pk}:{self.seats_reserved}"
        return hmac.new(ticket_key, payload.encode(), hashlib.sha256).hexdigest()[:16]

    def verify_security_signature(self, signature):
        """
        Vérifie si la signature fournie correspond à ce billet.
        Prend en charge la signature sécurisée actuelle et le format de compatibilité rétroactif.
        Supporte à la fois TICKET_SIGNING_KEY et SECRET_KEY (pour les anciens billets physiques).
        """
        import hmac
        import hashlib
        if not signature:
            return False
        
        expected = self.get_security_signature()
        if hmac.compare_digest(str(signature).lower(), expected.lower()):
            return True
            
        # Si TICKET_SIGNING_KEY est différent de SECRET_KEY, vérifier aussi avec SECRET_KEY (anciens billets émis)
        sec_key = getattr(settings, 'SECRET_KEY', '').encode()
        ticket_key = getattr(settings, 'TICKET_SIGNING_KEY', settings.SECRET_KEY).encode()
        if sec_key and sec_key != ticket_key:
            payload = f"EASYTRAVEL:{self.reference}:{self.pk}:{self.seats_reserved}"
            expected_sec = hmac.new(sec_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
            if hmac.compare_digest(str(signature).lower(), expected_sec.lower()):
                return True

        # Rétrocompatibilité avec l'ancien format legacy
        try:
            start_city = self.departure_stop.city.name if self.departure_stop and self.departure_stop.city else "Départ"
            end_city = self.arrival_stop.city.name if self.arrival_stop and self.arrival_stop.city else "Arrivée"
            dep_date = self.departure.date if self.departure else ""
            legacy_payload = f"REF:{self.reference}|NOM:{self.traveler_name}|PIECE:{self.id_type}-{self.id_number}|TRAJET:{start_city}-{end_city}|DATE:{dep_date}|PLACES:{self.seats_reserved}"
            legacy_sig1 = hmac.new(ticket_key, legacy_payload.encode(), hashlib.sha256).hexdigest()[:12]
            if hmac.compare_digest(str(signature).lower(), legacy_sig1.lower()):
                return True
            if sec_key and sec_key != ticket_key:
                legacy_sig2 = hmac.new(sec_key, legacy_payload.encode(), hashlib.sha256).hexdigest()[:12]
                if hmac.compare_digest(str(signature).lower(), legacy_sig2.lower()):
                    return True
            return False
        except Exception:
            return False

    def get_verification_url(self, request=None):
        """
        Construit l'URL publique officielle et sécurisée de vérification du billet.
        """
        from django.urls import reverse
        sig = self.get_security_signature()
        path = f"{reverse('bookings:verify_ticket', kwargs={'reference': self.reference})}?sig={sig}"
        if request:
            return request.build_absolute_uri(path)
        base_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com').rstrip('/')
        return f"{base_url}{path}"

    def generate_qr_code_base64(self, request=None):
        """
        Génère l'image du QR Code au format Data URI (PNG base64).
        Le QR code encode l'URL officielle certifiée EasyTravel, lisible par tout smartphone.
        """
        import qrcode
        from io import BytesIO
        import base64
        
        url = self.get_verification_url(request=request)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=3
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        qr_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{qr_b64}"

    @property
    def is_expired(self):
        """
        Indique si le billet est expiré :
        - Le départ est marqué comme parti ('departed') ou annulé ('cancelled')
        - Ou la date/heure de départ du bus est passée
        """
        if not self.departure:
            return True
        if self.departure.status in ['departed', 'cancelled']:
            return True
        from django.utils import timezone
        now = timezone.localtime()
        if self.departure.date < now.date():
            return True
        if self.departure.date == now.date() and self.departure.time <= now.time():
            return True
        return False

    @property
    def is_already_validated(self):
        """
        Indique si le titre de transport a déjà été validé / composté à l'embarquement
        """
        return self.status == 'boarded'

    @property
    def can_send_ticket_email(self):
        """
        Contrôle d'intégrité pour l'envoi de l'e-mail officiel du billet :
        1. Le billet doit être payé/encaissé ('paid')
        2. Le billet ne doit pas avoir été déjà validé ('boarded')
        3. Le billet ne doit pas être annulé ('cancelled')
        4. Le voyage ne doit pas être expiré dans le temps
        5. Une adresse e-mail doit exister
        Retourne (bool, reason_str).
        """
        if self.payment_status != 'paid':
            return False, "Le billet n'est pas encore payé/encaissé."
        if self.status == 'boarded':
            return False, "Le billet a déjà été validé à l'embarquement (passager à bord)."
        if self.status == 'cancelled':
            return False, "Le billet est annulé."
        if self.is_expired:
            return False, "Le voyage est expiré (date/heure de départ passée)."
        if not (self.traveler_email or (self.user and self.user.email)):
            return False, "Aucune adresse e-mail renseignée."
        return True, "Valide"

    @property
    def can_be_cancelled(self):
        """
        Règles d'intégrité pour l'annulation d'un billet :
        1. Le billet ne doit pas être déjà annulé
        2. Le passager ne doit pas avoir déjà embarqué (billet validé au quai)
        3. Le départ ne doit pas être expiré (date/heure de bus déjà passée)
        """
        if self.status == 'cancelled':
            return False, "La réservation est déjà annulée."
        if self.status == 'boarded':
            return False, "Impossible d'annuler : le billet a déjà été validé à l'embarquement."
        if self.is_expired:
            return False, "Impossible d'annuler : la date de voyage est expirée (bus déjà parti)."
        return True, "Annulable"

    def cancel(self, force=False):
        from django.db import transaction
        from travel.models import Departure
        
        can_cancel, reason = self.can_be_cancelled
        if not can_cancel and not force:
            return False
            
        with transaction.atomic():
            b = Booking.objects.select_for_update().get(pk=self.pk)
            if b.status != 'cancelled':
                if not force and b.status == 'boarded':
                    return False
                if not force and b.is_expired:
                    return False
                b.status = 'cancelled'
                # On ne remet des places dans le bus QUE si le départ est encore futur
                if b.departure and not b.is_expired:
                    dep = Departure.objects.select_for_update().get(pk=b.departure.pk)
                    dep.available_capacity += b.seats_reserved
                    dep.save(update_fields=['available_capacity'])
                b.save(update_fields=['status'])
                self.status = 'cancelled'
                return True
        return False

    @classmethod
    def cancel_expired_pending_bookings(cls):
        from django.utils import timezone
        from django.db import transaction
        from datetime import timedelta
        # On annule les réservations qui sont restées 'pending' plus de 15 minutes
        expiration_time = timezone.now() - timedelta(minutes=15)
        
        with transaction.atomic():
            expired_bookings = list(cls.objects.select_for_update().filter(
                status__in=['pending', 'confirmed'],
                payment_status='pending',
                created_at__lt=expiration_time
            ).select_related('departure'))
            
            count = 0
            for booking in expired_bookings:
                if booking.cancel(force=True):
                    booking.payment_status = 'failed'
                    booking.save(update_fields=['payment_status'])
                    count += 1
                
        return count

class BookingFeedback(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='feedback')
    rating = models.PositiveIntegerField(choices=[(i, str(i)) for i in range(1, 6)], help_text="Note sur 5")
    comment = models.TextField(blank=True, help_text="Commentaire du client")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Sondage de satisfaction"
        verbose_name_plural = "Sondages de satisfaction"

    def __str__(self):
        return f"Sondage pour {self.booking.reference} - {self.rating}/5"
