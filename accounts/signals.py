from django.contrib.sessions.models import Session
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
import logging

logger = logging.getLogger(__name__)

@receiver(user_logged_in)
def enforce_single_session(sender, user, request, **kwargs):
    """
    Assure qu'un utilisateur n'a qu'une seule session active à la fois.
    Opération O(1) : Invalide directement l'ancienne session
    enregistrée dans active_session_key sans scanner toute la table des sessions.
    """
    if not request or not hasattr(request, 'session'):
        return

    if not request.session.session_key:
        request.session.create()

    current_session_key = request.session.session_key
    old_session_key = getattr(user, 'active_session_key', '')

    if old_session_key and old_session_key != current_session_key:
        try:
            old_session = Session.objects.filter(session_key=old_session_key).first()
            if old_session:
                data = old_session.get_decoded()
                if '_auth_user_id' in data:
                    del data['_auth_user_id']
                if '_auth_user_backend' in data:
                    del data['_auth_user_backend']
                if '_auth_user_hash' in data:
                    del data['_auth_user_hash']
                data['kicked_out'] = True
                old_session.session_data = Session.objects.encode(data)
                old_session.save()
        except Exception as e:
            logger.warning(f"Erreur lors de l'invalidation de l'ancienne session {old_session_key}: {e}")

    if hasattr(user, 'active_session_key') and old_session_key != current_session_key:
        user.active_session_key = current_session_key
        user.save(update_fields=['active_session_key'])
