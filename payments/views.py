"""
Views for CinetPay Payment Processing in EasyTravel.
Handles payment initiation, CinetPay IPN webhook notification, and customer return redirection.
"""

import json
import uuid
import logging
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.contrib import messages
from django.conf import settings
from django.utils import timezone

from bookings.models import Booking, BookingCart
from core.tasks import dispatch_async_task
from core.ratelimit import ratelimit
from .models import PaymentTransaction
from .services.saspay import SasPayService

logger = logging.getLogger(__name__)


@ratelimit(max_requests=20, window_seconds=60)
def initiate_booking_payment(request, booking_id):
    """
    Initiates payment for a booking.
    If simulation mode is active or CinetPay is unconfigured, directs user to the Interactive Sandbox Checkout.
    Otherwise, initializes payment with CinetPay v2 and redirects to live checkout.
    """
    booking = get_object_or_404(Booking, id=booking_id)

    # Vérification IDOR : Seul le propriétaire peut payer
    if booking.user and booking.user != request.user:
        messages.error(request, "Accès non autorisé à cette transaction.")
        return redirect('travel:home')

    # If already paid, take directly to confirmed ticket
    if booking.payment_status == 'paid':
        messages.info(request, "Cette réservation a déjà été entièrement réglée.")
        return redirect('bookings:confirmation', reference=booking.reference)

    # Si l'utilisateur possède un solde portefeuille suffisant et n'a pas forcé le paiement externe en ligne (?gateway=live),
    # on le dirige vers l'écran de confirmation où le bouton de débit portefeuille 1-clic est immédiatement proposé !
    if request.user.is_authenticated and hasattr(request.user, 'wallet_balance') and request.user.wallet_balance >= booking.total_amount and request.GET.get('gateway') != 'live':
        return redirect('bookings:confirmation', reference=booking.reference)

    simulation_mode = getattr(settings, 'PAYMENT_SIMULATION_MODE', True)
    service = SasPayService()

    # Redirection vers le mode Simulation / Sandbox
    if simulation_mode or not service.is_configured():
        return redirect('payments:simulation_checkout', booking_id=booking.id)

    # Mode SasPay Réel (Live)
    return_url = getattr(settings, 'CINETPAY_RETURN_URL', '') or request.build_absolute_uri(reverse('payments:return'))
    notify_url = getattr(settings, 'CINETPAY_NOTIFY_URL', '') or request.build_absolute_uri(reverse('payments:notification'))

    # Store transaction context in session for return fallback
    request.session['last_payment_booking_ref'] = booking.reference

    success, result, tx_id = service.initiate_payment(
        booking=booking,
        return_url=return_url,
        notify_url=notify_url,
    )

    if tx_id:
        PaymentTransaction.objects.update_or_create(
            transaction_id=tx_id,
            defaults={
                'booking': booking,
                'user': booking.user or (request.user if request.user.is_authenticated else None),
                'provider': 'saspay',
                'amount': booking.total_amount,
                'currency': 'XAF',
                'status': 'pending',
                'raw_response': result if isinstance(result, dict) else {},
            }
        )

    if success:
        return redirect(result['payment_url'])
    else:
        messages.error(request, result.get('message', "Erreur d'initialisation du paiement."))
        return redirect('travel:home')


@ratelimit(max_requests=20, window_seconds=60)
def initiate_cart_payment(request, cart_id):
    """
    Initiates payment for an entire BookingCart.
    """
    cart = get_object_or_404(BookingCart, id=cart_id)

    # Vérification IDOR : Seul le propriétaire peut payer
    if cart.user and cart.user != request.user:
        messages.error(request, "Accès non autorisé à ce panier.")
        return redirect('travel:home')

    # If already paid, take directly to the first confirmed ticket
    if cart.payment_status == 'paid':
        messages.info(request, "Ce panier a déjà été entièrement réglé.")
        first_booking = cart.bookings.first()
        if first_booking:
            return redirect('bookings:confirmation', reference=first_booking.reference)
        return redirect('accounts:my_bookings')

    simulation_mode = getattr(settings, 'PAYMENT_SIMULATION_MODE', True)
    service = SasPayService()

    # Redirection vers le mode Simulation / Sandbox (NON GERE POUR PANIER ICI)
    if simulation_mode or not service.is_configured():
        # Fallback to paying the first booking in sandbox
        first_booking = cart.bookings.first()
        if not first_booking:
            return redirect('accounts:my_bookings')
        return redirect('payments:simulation_checkout', booking_id=first_booking.id)

    # Mode SasPay Réel (Live)
    return_url = getattr(settings, 'CINETPAY_RETURN_URL', '') or request.build_absolute_uri(reverse('payments:return'))
    notify_url = getattr(settings, 'CINETPAY_NOTIFY_URL', '') or request.build_absolute_uri(reverse('payments:notification'))

    # Store transaction context in session for return fallback
    first_booking = cart.bookings.first()
    if first_booking:
        request.session['last_payment_booking_ref'] = first_booking.reference

    success, result, tx_id = service.initiate_payment(
        cart=cart,
        return_url=return_url,
        notify_url=notify_url,
    )

    if tx_id:
        PaymentTransaction.objects.update_or_create(
            transaction_id=tx_id,
            defaults={
                'cart': cart,
                'user': cart.user or (request.user if request.user.is_authenticated else None),
                'provider': 'saspay',
                'amount': cart.total_amount,
                'currency': 'XAF',
                'status': 'pending',
                'raw_response': result if isinstance(result, dict) else {},
            }
        )

    if success and result.get('payment_url'):
        request.session['current_payment_tx_id'] = tx_id
        return redirect(result['payment_url'])
    else:
        error_msg = result.get('message', "Une erreur est survenue lors de l'initialisation du paiement.")
        messages.error(request, f"Paiement en ligne indisponible : {error_msg}")
        if first_booking:
            return redirect('bookings:confirmation', reference=first_booking.reference)
        return redirect('accounts:my_bookings')


def simulation_checkout(request, booking_id):
    """
    Renders the EasyTravel Interactive Simulation / Sandbox Payment Checkout page.
    Allows testing Orange Money, MTN MoMo, and Card transactions in a realistic sandbox environment.
    """
    booking = get_object_or_404(
        Booking.objects.select_related(
            'departure', 'departure__agency', 'departure__vehicle',
            'departure_stop__city', 'arrival_stop__city', 'user'
        ),
        id=booking_id
    )

    # Vérification IDOR
    if booking.user and booking.user != request.user:
        messages.error(request, "Accès non autorisé à cette transaction.")
        return redirect('travel:home')

    if booking.payment_status == 'paid':
        messages.info(request, "Cette réservation a déjà été entièrement réglée.")
        return redirect('bookings:confirmation', reference=booking.reference)

    start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
    end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"

    return render(request, 'payments/checkout_simulation.html', {
        'booking': booking,
        'departure': booking.departure,
        'agency': booking.departure.agency,
        'start_city': start_city,
        'end_city': end_city,
        'default_phone': booking.traveler_phone or '699000000',
    })


@login_required
@require_POST
@ratelimit(max_requests=10, window_seconds=60)
def pay_with_wallet(request, booking_id):
    """
    Règle une réservation en débitant directement le solde du portefeuille de l'utilisateur (wallet_balance).
    Vérification stricte, débit atomique en base avec select_for_update, émission immédiate du billet et envoi de l'email de confirmation.
    """
    booking = None
    try:
        from django.db import transaction
        from accounts.models import User
        from accounts.services.email_service import send_ticket_confirmation_email
        import threading

        booking = get_object_or_404(Booking, id=booking_id)

        # Vérification IDOR : Seul le propriétaire peut régler avec son portefeuille
        if booking.user and booking.user != request.user:
            messages.error(request, "Accès non autorisé à cette réservation.")
            return redirect('travel:home')

        # Si le voyageur n'était pas associé (cas réservation rapide), l'associer au compte actuel
        if not booking.user:
            booking.user = request.user
            booking.save(update_fields=['user'])

        if booking.payment_status == 'paid':
            messages.info(request, "Cette réservation a déjà été entièrement réglée.")
            return redirect('bookings:confirmation', reference=booking.reference)

        user = request.user
        required_amount = booking.total_amount or 0

        with transaction.atomic():
            # Verrouillage pessimiste de l'utilisateur pour éviter les conditions de concurrence / double débit
            db_user = User.objects.select_for_update().get(id=user.id)
            current_balance = getattr(db_user, 'wallet_balance', 0) or 0

            if current_balance < required_amount:
                messages.error(
                    request,
                    f"Solde insuffisant sur votre Portefeuille EasyTravel ({int(current_balance):,} FCFA). "
                    f"Le montant requis pour ce voyage est de {int(required_amount):,} FCFA."
                )
                return redirect('bookings:confirmation', reference=booking.reference)

            # Débit effectif du solde
            db_user.wallet_balance -= required_amount
            db_user.save(update_fields=['wallet_balance'])

            tx_id = f"WLT-{booking.id}-{uuid.uuid4().hex[:8].upper()}"

            PaymentTransaction.objects.create(
                transaction_id=tx_id,
                booking=booking if not booking.cart else None,
                cart=booking.cart,
                user=db_user,
                provider='wallet',
                payment_method='wallet',
                amount=required_amount,
                currency='XAF',
                status='success',
                completed_at=timezone.now(),
                raw_response={'description': f"Règlement Portefeuille pour {booking.reference}"}
            )

            if booking.cart:
                cart_bookings = Booking.objects.filter(cart=booking.cart)
                for b in cart_bookings:
                    b.payment_status = 'paid'
                    b.payment_method = 'wallet'
                    b.payment_operator = 'Portefeuille EasyTravel'
                    b.transaction_id = tx_id
                    b.status = 'confirmed'
                    b.paid_at = timezone.now()
                    b.save()
                booking.cart.payment_status = 'paid'
                booking.cart.transaction_id = tx_id
                booking.cart.save(update_fields=['payment_status', 'transaction_id'])
            else:
                booking.payment_status = 'paid'
                booking.payment_method = 'wallet'
                booking.payment_operator = 'Portefeuille EasyTravel'
                booking.transaction_id = tx_id
                booking.status = 'confirmed'
                booking.paid_at = timezone.now()
                booking.save()

        # Envoi de l'email officiel avec QR Code (en arrière-plan supervisé)
        try:
            dispatch_async_task(send_ticket_confirmation_email, request, booking)
        except Exception as e:
            logger.warning(f"Could not dispatch ticket confirmation email task: {e}")

        messages.success(
            request,
            f"Paiement validé avec succès ! Votre billet a été réglé depuis votre Portefeuille EasyTravel. "
            f"Montant débité : {int(required_amount):,} FCFA. Nouveau solde disponible : {int(db_user.wallet_balance):,} FCFA."
        )
        return redirect('bookings:confirmation', reference=booking.reference)

    except Exception as e:
        logger.exception(f"Erreur inattendue dans pay_with_wallet pour la réservation {booking_id}: {e}")
        messages.error(request, f"Une anomalie technique est survenue lors de l'opération : {e}")
        if booking and hasattr(booking, 'reference') and booking.reference:
            return redirect('bookings:confirmation', reference=booking.reference)
        return redirect('accounts:my_bookings')


@login_required
@require_POST
def simulation_process(request, booking_id):
    """
    Processes simulated payments (either successful or failed) for testing.
    Updates booking status, generates transaction ID and sends confirmation email.
    """
    payment_method = request.POST.get('payment_method', 'mtn_momo')
    if payment_method == 'wallet':
        return pay_with_wallet(request, booking_id)

    if not settings.PAYMENT_SIMULATION_MODE:
        raise Http404("Simulation de paiement désactivée.")
        
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)

    action = request.POST.get('action', 'success')
    phone = request.POST.get('phone', booking.traveler_phone)

    operator_labels = {
        'mtn_momo': 'MTN Mobile Money',
        'orange_money': 'Orange Money Cameroun',
        'card': 'Carte Bancaire (Visa/Mastercard)',
        'wallet': 'Portefeuille EasyTravel',
    }
    operator_name = operator_labels.get(payment_method, 'Mobile Money')

    if action == 'success':
        tx_id = f"SIM-TX-{booking.id}-{uuid.uuid4().hex[:8].upper()}"
        
        from django.db import transaction
        with transaction.atomic():
            PaymentTransaction.objects.create(
                transaction_id=tx_id,
                booking=booking if not booking.cart else None,
                cart=booking.cart,
                user=booking.user,
                provider='simulation',
                payment_method=payment_method,
                amount=booking.cart.total_amount if booking.cart else booking.total_amount,
                currency='XAF',
                status='success',
                completed_at=timezone.now(),
                raw_response={'operator': operator_name, 'phone': phone, 'action': 'success'}
            )

            if booking.cart:
                cart_bookings = Booking.objects.filter(cart=booking.cart)
                for b in cart_bookings:
                    b.payment_status = 'paid'
                    b.payment_method = payment_method
                    b.payment_operator = f"{operator_name} (Simulation)"
                    b.transaction_id = tx_id
                    b.status = 'confirmed'
                    b.paid_at = timezone.now()
                    b.save()
                booking.cart.payment_status = 'paid'
                booking.cart.transaction_id = tx_id
                booking.cart.save(update_fields=['payment_status', 'transaction_id'])
            else:
                booking.payment_status = 'paid'
                booking.payment_method = payment_method
                booking.payment_operator = f"{operator_name} (Simulation)"
                booking.transaction_id = tx_id
                booking.status = 'confirmed'
                booking.paid_at = timezone.now()
                booking.save()

        # Send ticket confirmation email in background
        try:
            from accounts.services.email_service import send_ticket_confirmation_email
            dispatch_async_task(send_ticket_confirmation_email, request, booking)
        except Exception as e:
            logger.warning(f"Could not send ticket confirmation email: {e}")

        messages.success(
            request, 
            f"🎉 Paiement simulé de {int(booking.total_amount):,} FCFA validé avec succès via {operator_name} ! "
            f"Votre titre de transport avec QR Code officiel est disponible ci-dessous."
        )
        return redirect('bookings:confirmation', reference=booking.reference)

    else:
        tx_id = f"SIM-TX-{booking.id}-{uuid.uuid4().hex[:8].upper()}-FAIL"
        from django.db import transaction
        with transaction.atomic():
            PaymentTransaction.objects.create(
                transaction_id=tx_id,
                booking=booking if not booking.cart else None,
                cart=booking.cart,
                user=booking.user,
                provider='simulation',
                payment_method=payment_method,
                amount=booking.cart.total_amount if booking.cart else booking.total_amount,
                currency='XAF',
                status='failed',
                raw_response={'operator': operator_name, 'phone': phone, 'action': 'failed'}
            )

            if booking.cart:
                Booking.objects.filter(cart=booking.cart).update(payment_status='failed')
                booking.cart.payment_status = 'failed'
                booking.cart.save(update_fields=['payment_status'])
            else:
                booking.payment_status = 'failed'
                booking.save(update_fields=['payment_status'])
        messages.error(
            request,
            f"❌ Simulation : La transaction via {operator_name} a été refusée ou interrompue (Solde insuffisant / Annulation USSD). Vous pouvez retenter le règlement."
        )
        return redirect('bookings:confirmation', reference=booking.reference)



@csrf_exempt
@require_POST
def saspay_notification(request):
    """
    Webhook SasPay (IPN).
    Vérifie la signature et met à jour le statut.
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid JSON")

    service = SasPayService()
    
    # Verify Webhook Signature
    signature = request.headers.get('X-Webhook-Signature', '')
    timestamp = request.headers.get('X-Webhook-Timestamp', '')
    
    if not service.verify_webhook_signature(request.body, signature, timestamp):
        logger.warning("Invalid SasPay webhook signature or timestamp.")
        return HttpResponseBadRequest("Invalid signature")

    event = data.get('event')
    if event == 'webhook.test':
        return JsonResponse({"status": "success", "message": "Test OK"}, status=200)
    
    if event not in ['transaction.success', 'transaction.failed', 'transaction.cancelled']:
        return JsonResponse({"status": "ignored"}, status=200)

    payload_data = data.get('data', {})
    
    # PROJECT FILTERING
    # If the merchant has multiple projects, ignore webhooks not meant for us
    expected_project_id = getattr(settings, 'SASPAY_PROJECT_ID', 'easytravel')
    metadata = payload_data.get('metadata') if isinstance(payload_data.get('metadata'), dict) else {}
    incoming_project_id = metadata.get('project')
    if incoming_project_id and incoming_project_id != expected_project_id:
        logger.info(f"SasPay Webhook ignoré (projet: {incoming_project_id}, attendu: {expected_project_id})")
        return HttpResponse('Ignored', status=200)

    updated, booking, message = service.verify_and_update_booking_webhook(payload_data)

    if updated and booking and event == 'transaction.success':
        try:
            from accounts.services.email_service import send_ticket_confirmation_email
            if hasattr(booking, 'bookings'):
                for b in booking.bookings.all():
                    dispatch_async_task(send_ticket_confirmation_email, request, b)
            else:
                dispatch_async_task(send_ticket_confirmation_email, request, booking)
        except Exception as e:
            logger.warning(f"Could not send ticket confirmation email during IPN: {e}")

    return JsonResponse({
        "status": "success" if updated else "pending_or_failed",
        "message": message,
    }, status=200)


def saspay_return(request):
    """
    User landing page when redirected back from SasPay Checkout.
    Verifies transaction status and redirects to official boarding pass.
    """
    tx_id = request.GET.get('transaction_id') or request.session.get('current_payment_tx_id')

    obj = None
    if tx_id:
        try:
            service = SasPayService()
            # Fallback manual verification if webhook is delayed
            success, payment_data = service.verify_payment(tx_id)
            if success and isinstance(payment_data, dict):
                # Ensure transaction_id is present for lookup
                if not payment_data.get('transaction_id'):
                    meta = payment_data.get('metadata')
                    if not isinstance(meta, dict) or not meta.get('transaction_id'):
                        payment_data['transaction_id'] = tx_id
                updated, obj, message = service.verify_and_update_booking_webhook(payment_data)
        except Exception as e:
            logger.exception(f"Erreur lors de la vérification SasPay au retour pour {tx_id}: {e}")
            obj = None

    if obj:
        if obj.payment_status == 'paid':
            messages.success(request, "🎉 Votre paiement a été validé avec succès ! Votre(vos) billet(s) est(sont) confirmé(s).")
        elif obj.payment_status == 'failed':
            messages.error(request, "Le paiement a été interrompu ou refusé par l'opérateur. Vous pouvez réessayer.")
        else:
            messages.info(request, "Votre transaction est en cours de finalisation par votre opérateur Mobile Money.")
        
        if hasattr(obj, 'bookings'): # It's a Cart
            first = obj.bookings.first()
            if first:
                return redirect('bookings:confirmation', reference=first.reference)
            return redirect('accounts:my_bookings')
        else:
            return redirect('bookings:confirmation', reference=obj.reference)

    # Fallback to session reference if available
    ref = request.session.get('last_payment_booking_ref')
    if ref:
        booking = Booking.objects.filter(reference=ref).first()
        if booking:
            return redirect('bookings:confirmation', reference=booking.reference)

    messages.info(request, "Transaction terminée. Consultez vos réservations ci-dessous.")
    return redirect('accounts:my_bookings')
