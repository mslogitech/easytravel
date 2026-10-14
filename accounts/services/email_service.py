import logging
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.urls import reverse
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from accounts.models import User
from core.tasks import dispatch_async_task

from urllib.parse import quote

logger = logging.getLogger(__name__)

def send_mail_async(*args, **kwargs):
    dispatch_async_task(send_mail, *args, **kwargs)



def generate_activation_link(request, user, next_url=None):
    """
    Génère un lien d'activation sécurisé, unique et à usage unique pour l'utilisateur,
    en conservant l'URL de redirection (ex: finalisation de réservation).
    """
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    relative_path = reverse('accounts:activate_account', kwargs={'uidb64': uidb64, 'token': token})
    
    if next_url and next_url != 'travel:home':
        separator = '&' if '?' in relative_path else '?'
        relative_path += f"{separator}next={quote(next_url)}"
    
    if request:
        return request.build_absolute_uri(relative_path)
    
    base_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com').rstrip('/')
    return f"{base_url}{relative_path}"


def send_account_activation_email(request, user, next_url=None, booking=None):
    """
    Envoie un email HTML et texte contenant le lien d'activation sécurisé du compte voyageur.
    Intègre les détails du voyage si la création de compte est initiée lors d'une réservation.
    """
    try:
        activation_url = generate_activation_link(request, user, next_url=next_url)
        context = {
            'user': user,
            'activation_url': activation_url,
            'booking': booking,
            'site_name': 'EasyTravel',
            'support_email': getattr(settings, 'SUPPORT_EMAIL', 'support@easytravel.mslogitech.com'),
        }

        if booking:
            subject = f"[EasyTravel] Validez votre compte pour confirmer votre billet {booking.reference}"
        elif next_url and '/reservations/' in next_url:
            subject = "[EasyTravel] Activez votre compte voyageur pour finaliser votre réservation"
        else:
            subject = "[EasyTravel] Activez votre compte voyageur et confirmez votre adresse email"

        html_message = render_to_string('accounts/emails/activation_email.html', context)
        text_message = render_to_string('accounts/emails/activation_email.txt', context)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')
        
        send_mail_async(
            subject=subject,
            message=text_message,
            from_email=from_email,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False
        )
        logger.info(f"Email d'activation envoyé avec succès à {user.email} (ID: {user.pk}, Booking: {getattr(booking, 'reference', None)})")
        return True
    except Exception as e:
        logger.error(f"Échec de l'envoi de l'email d'activation à {user.email}: {e}", exc_info=True)
        return False



def verify_activation_token(uidb64, token):
    """
    Vérifie l'authenticité et la validité du token d'activation.
    Retourne l'objet User si valide, sinon None.
    """
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None

    if user and default_token_generator.check_token(user, token):
        return user
    return None


def send_ticket_confirmation_email(request, booking, force=False):
    """
    Envoie un email de confirmation de paiement et délivrance de billet officiel au voyageur.
    Refuse l'envoi si le billet a déjà été validé à l'embarquement ou si la date de voyage est expirée.
    """
    can_send, reason = booking.can_send_ticket_email
    if not can_send and not force:
        logger.warning(f"Refus d'envoi d'e-mail de billet pour la réservation {booking.reference} : {reason}")
        return False

    recipient_email = booking.traveler_email or (booking.user.email if booking.user else None)
    if not recipient_email:
        logger.info(f"Aucune adresse email trouvée pour la réservation {booking.reference}, email non envoyé.")
        return False

    try:
        relative_path = reverse('bookings:confirmation', kwargs={'reference': booking.reference})
        if request:
            ticket_url = request.build_absolute_uri(relative_path)
        else:
            base_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com').rstrip('/')
            ticket_url = f"{base_url}{relative_path}"

        start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
        end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"

        import hmac, hashlib, qrcode, base64
        from io import BytesIO
        from xhtml2pdf import pisa
        from django.core.mail import EmailMultiAlternatives
        
        # Générer QR Code officiel avec URL de vérification et signature cryptographique
        qr_code_url = booking.generate_qr_code_base64(request=request)

        context = {
            'booking': booking,
            'ticket_url': ticket_url,
            'start_city': start_city,
            'end_city': end_city,
            'qr_code_url': qr_code_url,
            'support_email': getattr(settings, 'SUPPORT_EMAIL', 'support@easytravel.mslogitech.com'),
        }
        
        # 1. Générer le PDF
        pdf_html = render_to_string('bookings/pdf_ticket.html', context)
        pdf_result = BytesIO()
        pisa_status = pisa.CreatePDF(BytesIO(pdf_html.encode('UTF-8')), dest=pdf_result)

        subject = f"[EasyTravel] Billet Confirmé & Payé : {start_city} → {end_city} (Réf: {booking.reference})"
        html_message = render_to_string('accounts/emails/ticket_paid_email.html', context)
        text_message = render_to_string('accounts/emails/ticket_paid_email.txt', context)
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=from_email,
            to=[recipient_email]
        )
        email.attach_alternative(html_message, "text/html")
        
        # 2. Attacher le PDF s'il a bien été généré
        if not pisa_status.err:
            email.attach(f"Billet_{booking.reference}.pdf", pdf_result.getvalue(), "application/pdf")
            
        email.send(fail_silently=True)

        logger.info(f"Email de confirmation de billet envoyé à {recipient_email} pour la réservation {booking.reference}.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'email de confirmation du billet {booking.reference} à {recipient_email}: {e}", exc_info=True)
        return False

def send_trip_reminder_email(booking):
    """Envoie un email de rappel de voyage au passager"""
    if not booking.traveler_email:
        return False
        
    try:
        start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
        end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"

        context = {
            'booking': booking,
            'start_city': start_city,
            'end_city': end_city,
        }

        subject = f"[Rappel] Votre voyage vers {end_city} avec {booking.departure.agency.name}"
        html_message = render_to_string('accounts/emails/trip_reminder_email.html', context)
        text_message = render_to_string('accounts/emails/trip_reminder_email.txt', context)
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')

        send_mail_async(
            subject=subject,
            message=text_message,
            from_email=from_email,
            recipient_list=[booking.traveler_email],
            html_message=html_message,
            fail_silently=True
        )
        logger.info(f"Email de rappel envoyé à {booking.traveler_email} pour la réservation {booking.reference}.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi du rappel pour le billet {booking.reference} à {booking.traveler_email}: {e}", exc_info=True)
        return False

def send_satisfaction_survey_email(request, booking):
    """Envoie un email de sondage de satisfaction après un voyage"""
    if not booking.traveler_email:
        return False
        
    try:
        start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
        end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"
        
        # S'il y a un 'request', on génère depuis le site, sinon depuis le CRON on prend la var SITE_URL
        from django.urls import reverse
        if request:
            survey_url = request.build_absolute_uri(reverse('bookings:feedback', kwargs={'reference': booking.reference}))
        else:
            site_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com')
            survey_url = f"{site_url}{reverse('bookings:feedback', kwargs={'reference': booking.reference})}"

        context = {
            'booking': booking,
            'start_city': start_city,
            'end_city': end_city,
            'survey_url': survey_url,
        }

        subject = f"Comment s'est passé votre voyage vers {end_city} avec {booking.departure.agency.name} ?"
        html_message = render_to_string('accounts/emails/satisfaction_survey_email.html', context)
        text_message = render_to_string('accounts/emails/satisfaction_survey_email.txt', context)
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')

        send_mail_async(
            subject=subject,
            message=text_message,
            from_email=from_email,
            recipient_list=[booking.traveler_email],
            html_message=html_message,
            fail_silently=True
        )
        logger.info(f"Sondage de satisfaction envoyé à {booking.traveler_email} pour la réservation {booking.reference}.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi du sondage pour le billet {booking.reference} à {booking.traveler_email}: {e}", exc_info=True)
        return False


def send_missed_trip_email(booking):
    """Envoie un email de rattrapage à un client qui a manqué son bus (non scanné)"""
    if not booking.traveler_email:
        return False
        
    try:
        start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
        end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"
        
        site_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com')

        context = {
            'booking': booking,
            'start_city': start_city,
            'end_city': end_city,
            'site_url': site_url,
        }

        subject = f"Oups ! Vous avez manqué votre bus avec {booking.departure.agency.name} ?"
        html_message = render_to_string('accounts/emails/missed_trip_email.html', context)
        text_message = render_to_string('accounts/emails/missed_trip_email.txt', context)
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')

        send_mail_async(
            subject=subject,
            message=text_message,
            from_email=from_email,
            recipient_list=[booking.traveler_email],
            html_message=html_message,
            fail_silently=True
        )
        logger.info(f"Email de voyage manqué envoyé à {booking.traveler_email} pour la réservation {booking.reference}.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'email de voyage manqué pour le billet {booking.reference} à {booking.traveler_email}: {e}", exc_info=True)
        return False

def send_cancellation_email(booking):
    """Envoie un email confirmant l'annulation et le remboursement sur le portefeuille"""
    if not booking.traveler_email:
        return False
        
    try:
        start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
        end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"
        
        site_url = getattr(settings, 'SITE_URL', 'https://easytravel.mslogitech.com')

        context = {
            'booking': booking,
            'start_city': start_city,
            'end_city': end_city,
            'site_url': site_url,
        }

        subject = f"[EasyTravel] Annulation et Remboursement de votre billet {booking.reference}"
        html_message = render_to_string('accounts/emails/cancellation_email.html', context)
        text_message = render_to_string('accounts/emails/cancellation_email.txt', context)
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')

        send_mail_async(
            subject=subject,
            message=text_message,
            from_email=from_email,
            recipient_list=[booking.traveler_email],
            html_message=html_message,
            fail_silently=True
        )
        logger.info(f"Email d'annulation envoyé à {booking.traveler_email} pour la réservation {booking.reference}.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'email d'annulation pour le billet {booking.reference} à {booking.traveler_email}: {e}", exc_info=True)
        return False

