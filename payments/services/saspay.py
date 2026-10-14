import hmac
import hashlib
import time
import requests
import logging
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

class SasPayService:
    def __init__(self):
        self.api_key = getattr(settings, 'SASPAY_API_KEY', '').strip()
        self.webhook_secret = getattr(settings, 'SASPAY_WEBHOOK_SECRET', '').strip()
        self.base_url = getattr(settings, 'SASPAY_BASE_URL', 'https://api.saspay.me/api/v1').strip().rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def is_configured(self):
        return bool(self.api_key and self.webhook_secret)

    def initiate_payment(self, cart=None, booking=None, return_url=None, notify_url=None):
        import uuid
        import re
        
        if cart:
            first_booking = cart.bookings.first()
            if not first_booking:
                return False, {"message": "Le panier ne contient aucune réservation."}, ""
            tx_id = f"ET-CRT-{cart.reference}-{uuid.uuid4().hex[:4].upper()}"
            cart.transaction_id = tx_id
            cart.save(update_fields=['transaction_id'])
            amount = float(cart.total_amount)
            description = f"Panier EasyTravel Ref {cart.reference}"
            cart.bookings.update(transaction_id=tx_id, payment_operator="saspay")
        elif booking:
            tx_id = f"ET-TX-{booking.id}-{uuid.uuid4().hex[:8].upper()}"
            booking.transaction_id = tx_id
            booking.payment_operator = "saspay"
            booking.save(update_fields=['transaction_id', 'payment_operator'])
            first_booking = booking
            amount = float(booking.total_amount)
            description = f"Billet EasyTravel Ref {booking.reference} - {booking.traveler_name}"
        else:
            return False, {"message": "Aucune réservation fournie."}, ""

        customer_name = (getattr(first_booking, 'traveler_name', '') or "Passager").strip()
        customer_email = getattr(first_booking.user, 'email', '') if (first_booking and first_booking.user) else "no-reply@easytravel.cm"

        # Extraction et nettoyage du numéro de téléphone camerounais au format international (+2376XXXXXXXX)
        raw_phone = (
            getattr(first_booking, 'traveler_phone', '') 
            or (first_booking.user.phone if first_booking.user else '') 
            or ''
        ).strip()
        clean_digits = re.sub(r'\D', '', raw_phone)
        if clean_digits.startswith('237') and len(clean_digits) == 12:
            formatted_phone = f"+{clean_digits}"
        elif len(clean_digits) == 9:
            formatted_phone = f"+237{clean_digits}"
        elif clean_digits:
            formatted_phone = f"+{clean_digits}"
        else:
            formatted_phone = ""

        success, result = self.create_checkout_session(
            transaction_id=tx_id,
            amount=amount,
            currency="XAF",
            description=description,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=formatted_phone,
            country="CM",
            return_url=return_url
        )
        
        # In SasPay, the checkout URL is typically in result['checkout_url']
        if success and 'checkout_url' in result:
            return True, {'payment_url': result['checkout_url']}, tx_id
        
        return False, result, tx_id

    def create_checkout_session(
        self,
        transaction_id,
        amount,
        currency,
        description,
        customer_name,
        customer_email,
        return_url,
        customer_phone="",
        country="CM",
        fee_charge_mode="DEDUCTED"
    ):
        # Format the amount to string with 2 decimal places as required by SasPay API
        formatted_amount = f"{float(amount):.2f}"
        
        payload = {
            "amount": formatted_amount,
            "currency": currency,
            "country": country,
            "description": description,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "return_url": return_url,
            "fee_charge_mode": fee_charge_mode,
            "metadata": {
                "transaction_id": transaction_id,
                "project": getattr(settings, 'SASPAY_PROJECT_ID', 'easytravel').strip()
            }
        }
        if customer_phone:
            payload["customer_phone"] = customer_phone
        
        try:
            url = f"{self.base_url}/checkout-sessions/"
            resp = requests.post(url, json=payload, headers=self.headers, timeout=20)
            data = resp.json()
            
            # API returns success in response data, usually code 200/201 or data wrapper
            if resp.status_code in [200, 201]:
                return True, data.get('data', {}) if 'data' in data else data
            logger.error(f"Erreur création SasPay checkout: {data}")
            return False, data
        except requests.exceptions.RequestException as e:
            logger.exception("Exception réseau lors de la création SasPay checkout")
            return False, {"message": str(e)}
        except Exception as e:
            logger.exception("Exception inattendue lors de l'appel SasPay")
            return False, {"message": str(e)}

    def verify_payment(self, payment_id):
        try:
            url = f"{self.base_url}/payments/{payment_id}/verify/"
            resp = requests.get(url, headers=self.headers, timeout=20)
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text}
            if resp.status_code == 200:
                return True, data.get('data', {}) if isinstance(data, dict) and 'data' in data else data
            return False, data
        except requests.exceptions.RequestException as e:
            logger.exception("Erreur vérification SasPay")
            return False, {"message": str(e)}
        except Exception as e:
            logger.exception("Exception inattendue vérification SasPay")
            return False, {"message": str(e)}

    def verify_webhook_signature(self, raw_body: bytes, signature: str, timestamp: str) -> bool:
        if not self.webhook_secret or not signature or not timestamp:
            return False
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
            signed = f"{timestamp}.".encode() + raw_body
            expected = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception:
            return False

    def verify_and_update_booking_webhook(self, data):
        """
        Updates booking based on verified webhook data.
        Returns: (updated: bool, obj: Booking | BookingCart | None, message: str)
        """
        if not data or not isinstance(data, dict):
            return False, None, "Données de paiement invalides ou manquantes."

        metadata = data.get('metadata')
        if not isinstance(metadata, dict):
            metadata = {}

        transaction_id = metadata.get('transaction_id') or data.get('transaction_id') or data.get('id')
        status = str(data.get('status') or '').upper()
        
        raw_amount = data.get('net_amount') or data.get('charged') or data.get('amount') or 0
        try:
            pay_amount = float(raw_amount)
        except (ValueError, TypeError):
            pay_amount = 0.0

        operator = data.get('payment_method') or data.get('channel') or 'SasPay'
        
        if not transaction_id:
            return False, None, "transaction_id manquant dans les données de retour"

        from bookings.models import BookingCart, Booking
        from django.db import transaction
        
        is_cart = str(transaction_id).startswith('ET-CRT-')

        with transaction.atomic():
            if is_cart:
                obj = BookingCart.objects.select_for_update().filter(transaction_id=transaction_id).first()
                if not obj:
                    return False, None, "Panier introuvable."
                if obj.payment_status == 'paid':
                    return True, obj, "Panier déjà réglé."
            else:
                obj = Booking.objects.select_for_update().filter(transaction_id=transaction_id).first()
                if not obj:
                    return False, None, "Réservation introuvable."
                if obj.payment_status == 'paid':
                    return True, obj, "Réservation déjà réglée."

            expected_amount = float(obj.total_amount)
            
            if status == 'SUCCESS':
                if pay_amount < expected_amount:
                    # Allow slight variations or handle strictly? We'll log it.
                    logger.warning(f"Montant inférieur {pay_amount} < {expected_amount}")
                    
                obj.payment_status = 'paid'
                if not is_cart:
                    obj.payment_method = 'online'
                    obj.payment_operator = str(operator)
                    obj.status = 'confirmed'
                    obj.paid_at = timezone.now()
                    obj.save(update_fields=['payment_status', 'payment_method', 'payment_operator', 'status', 'paid_at'])
                else:
                    obj.save(update_fields=['payment_status'])
                    for b in obj.bookings.all():
                        b.payment_status = 'paid'
                        b.payment_method = 'online'
                        b.payment_operator = str(operator)
                        b.status = 'confirmed'
                        b.paid_at = timezone.now()
                        b.save(update_fields=['payment_status', 'payment_method', 'payment_operator', 'status', 'paid_at'])

                return True, obj, "Paiement validé avec succès (SasPay)."
                
            elif status in ('FAILED', 'CANCELLED'):
                obj.payment_status = 'failed'
                obj.save(update_fields=['payment_status'])
                if is_cart:
                    obj.bookings.update(payment_status='failed')
                return False, obj, f"Paiement échoué ({status})."
                
        return False, obj, f"Statut ignoré ({status})."
