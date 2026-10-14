from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Administrateur'),
        ('manager', "Responsable d'agence"),
        ('agent', "Agent d'agence"),
        ('traveler', 'Voyageur'),
    )
    ID_TYPE_CHOICES = (
        ('CNI', "Carte Nationale d'Identité (CNI)"),
        ('PASSPORT', "Passeport"),
        ('RECEIPT', "Récépissé de CNI"),
        ('STUDENT', "Carte d'étudiant / d'élève"),
        ('OTHER', "Autre (Aucune pièce officielle)"),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='traveler')
    email = models.EmailField('email address', unique=True, null=True, blank=True)
    phone = models.CharField(max_length=25, blank=True)
    id_type = models.CharField(max_length=20, choices=ID_TYPE_CHOICES, default='CNI', blank=True)
    id_number = models.CharField(max_length=50, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    agency = models.ForeignKey(
        'agencies.Agency', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='staff_members',
        help_text="L'agence à laquelle cet employé est rattaché (pour managers/agents)."
    )
    wallet_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0, 
        verbose_name="Solde Portefeuille"
    )

    def __str__(self):
        return self.username

# ---- GESTION DES SESSIONS MULTIPLES ----
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

@receiver(user_logged_in)
def remove_other_sessions(sender, user, request, **kwargs):
    """
    Empêche un utilisateur d'être connecté sur plusieurs appareils en même temps.
    Lorsqu'il se connecte, toutes ses anciennes sessions sont supprimées.
    """
    # Si la session n'est pas encore sauvegardée, on s'assure qu'elle l'est
    if not request.session.session_key:
        request.session.create()
        
    current_session_key = request.session.session_key
    
    # Parcourir toutes les sessions actives dans la base de données
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        data = session.get_decoded()
        # Vérifier si la session appartient à l'utilisateur qui vient de se connecter
        if str(data.get('_auth_user_id', '')) == str(user.id):
            # Si ce n'est pas la session actuelle, on modifie la session pour déconnecter l'utilisateur
            if session.session_key != current_session_key:
                # Retirer l'utilisateur de la session (déconnexion)
                if '_auth_user_id' in data:
                    del data['_auth_user_id']
                if '_auth_user_backend' in data:
                    del data['_auth_user_backend']
                if '_auth_user_hash' in data:
                    del data['_auth_user_hash']
                
                # Ajouter un marqueur pour indiquer qu'il a été expulsé
                data['kicked_out'] = True
                
                # Enregistrer la session modifiée
                session.session_data = Session.objects.encode(data)
                session.save()


class SystemAlert(models.Model):
    LEVEL_CHOICES = (
        ('info', 'Information'),
        ('warning', 'Avertissement'),
        ('error', 'Erreur Critique'),
    )
    title = models.CharField(max_length=255, verbose_name="Titre de l'alerte")
    message = models.TextField(verbose_name="Détails du problème")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='error', verbose_name="Niveau de gravité")
    is_resolved = models.BooleanField(default=False, verbose_name="Résolu ?")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Alerte Système"
        verbose_name_plural = "Alertes Système"
        
    def __str__(self):
        return f"[{self.get_level_display()}] {self.title}"
