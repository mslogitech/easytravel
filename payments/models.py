from django.db import models
from django.conf import settings
from django.utils import timezone
from bookings.models import Booking, BookingCart


class PaymentTransaction(models.Model):
    """
    Journal d'audit et registre de traçabilité immuable pour toutes les transactions de paiement.
    Enregistre chaque tentative, confirmation, rejet et webhook avant toute mutation d'état de réservation.
    """
    PROVIDER_CHOICES = (
        ('saspay', 'SasPay'),
        ('cinetpay', 'CinetPay'),
        ('campay', 'Campay'),
        ('wallet', 'Portefeuille EasyTravel'),
        ('cash', 'Espèces (Guichet)'),
        ('simulation', 'Simulation / Sandbox'),
    )

    STATUS_CHOICES = (
        ('pending', 'En attente'),
        ('success', 'Succès / Réglé'),
        ('failed', 'Échoué / Refusé'),
        ('cancelled', 'Annulé'),
        ('refunded', 'Remboursé'),
    )

    booking = models.ForeignKey(
        Booking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_transactions',
        verbose_name="Réservation associée"
    )
    cart = models.ForeignKey(
        BookingCart,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_transactions',
        verbose_name="Panier associé"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_transactions',
        verbose_name="Utilisateur / Payeur"
    )
    provider = models.CharField(
        max_length=30,
        choices=PROVIDER_CHOICES,
        default='saspay',
        verbose_name="Fournisseur / Passerelle"
    )
    payment_method = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name="Moyen de paiement"
    )
    transaction_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name="Identifiant unique de transaction"
    )
    provider_reference = models.CharField(
        max_length=150,
        blank=True,
        default='',
        db_index=True,
        verbose_name="Référence externe passerelle"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Montant (FCFA)"
    )
    currency = models.CharField(
        max_length=10,
        default='XAF',
        verbose_name="Devise"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
        verbose_name="Statut du paiement"
    )
    raw_response = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name="Données brutes / Réponse passerelle"
    )
    error_message = models.TextField(
        blank=True,
        default='',
        verbose_name="Message d'erreur"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date d'initiation"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Dernière mise à jour"
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de confirmation/règlement"
    )

    class Meta:
        verbose_name = "Transaction de Paiement"
        verbose_name_plural = "Transactions de Paiement"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.provider.upper()}] {self.transaction_id} - {self.amount} {self.currency} ({self.get_status_display()})"
