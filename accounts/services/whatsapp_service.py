import logging
import requests
from django.conf import settings
from .email_service import generate_activation_link

logger = logging.getLogger(__name__)

def send_whatsapp_message(to_phone, message_text):
    """
    Simule ou envoie un message WhatsApp via l'API officielle (Meta) ou un fournisseur (Twilio, Infobip).
    Pour l'instant, c'est un mock qui écrit dans les logs en attendant vos identifiants API WhatsApp.
    """
    to_phone = str(to_phone).replace(" ", "").replace("+", "")
    
    # MOCK (En attendant l'API Key WhatsApp Meta)
    logger.info(f"--- MOCK WHATSAPP ---")
    logger.info(f"TO: {to_phone}")
    logger.info(f"MESSAGE: \n{message_text}")
    logger.info(f"---------------------")
    try:
        print(f"[WHATSAPP SIMULATION] Message envoyé à {to_phone}: {message_text}")
    except UnicodeEncodeError:
        print(f"[WHATSAPP SIMULATION] Message envoyé à {to_phone}: {message_text.encode('ascii', errors='replace').decode('ascii')}")
    
    # Implémentation réelle Meta API (décommenter quand vous aurez les clés)
    """
    WHATSAPP_TOKEN = getattr(settings, 'WHATSAPP_TOKEN', '')
    WHATSAPP_PHONE_ID = getattr(settings, 'WHATSAPP_PHONE_ID', '')
    
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message_text}
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code != 200:
            logger.error(f"Erreur API WhatsApp: {response.text}")
    except Exception as e:
        logger.error(f"Exception WhatsApp: {e}")
    """

def send_whatsapp_activation(user, request=None, next_url=None):
    """
    Envoie le lien d'activation de compte par WhatsApp.
    """
    activation_url = generate_activation_link(request, user, next_url)
    message = (
        f"Bienvenue sur EasyTravel {user.first_name} ! 🚍\n\n"
        f"Veuillez cliquer sur ce lien sécurisé pour activer votre compte "
        f"et finaliser votre opération :\n\n"
        f"👉 {activation_url}\n\n"
        f"Ce lien expirera dans quelques heures.\n"
        f"L'équipe EasyTravel."
    )
    send_whatsapp_message(user.phone, message)

def send_whatsapp_booking_confirmation(booking):
    """
    Envoie un reçu de réservation par WhatsApp.
    """
    message = (
        f"✅ Réservation Confirmée - EasyTravel\n\n"
        f"Bonjour {booking.traveler_name},\n"
        f"Votre voyage de {booking.departure.line.departure_city.name} vers {booking.departure.line.arrival_city.name} "
        f"est confirmé pour le {booking.departure.date.strftime('%d/%m/%Y')} à {booking.departure.time.strftime('%H:%M')}.\n\n"
        f"Billet N° {booking.reference}\n"
        f"Agence : {booking.departure.agency.name}\n\n"
        f"Bon voyage !"
    )
    send_whatsapp_message(booking.traveler_phone, message)
