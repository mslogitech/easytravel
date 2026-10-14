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
    active_session_key = models.CharField(
        max_length=40,
        blank=True,
        default="",
        verbose_name="Clé de session active"
    )

    def __str__(self):
        return self.username


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
