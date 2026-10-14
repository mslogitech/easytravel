from django import forms
from django.urls import path
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib import admin
from accounts.models import User
from travel.models import Departure, LineStop
from .models import Booking, BookingFeedback, normalize_id_type

@admin.register(BookingFeedback)
class BookingFeedbackAdmin(admin.ModelAdmin):
    list_display = ('booking', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('booking__reference', 'comment')
    readonly_fields = ('booking', 'rating', 'comment', 'created_at')


class BookingAdminForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Rendre les champs passagers et montants facultatifs lors de la saisie (calculés automatiquement)
        self.fields['traveler_name'].required = False
        self.fields['traveler_phone'].required = False
        self.fields['id_type'].required = False
        self.fields['id_number'].required = False
        self.fields['total_amount'].required = False
        self.fields['agency_amount'].required = False
        self.fields['platform_fee'].required = False
        self.fields['departure_stop'].required = False
        self.fields['arrival_stop'].required = False

        # Filtrage contextuel des arrêts si un départ est déjà sélectionné
        selected_departure_id = None
        if self.instance and self.instance.pk and self.instance.departure_id:
            selected_departure_id = self.instance.departure_id
        elif self.data and str(self.data.get('departure')).isdigit():
            selected_departure_id = int(self.data.get('departure'))

        if selected_departure_id:
            try:
                departure = Departure.objects.select_related('line').get(pk=selected_departure_id)
                stops_qs = departure.line.stops.select_related('city').order_by('stop_order')
                self.fields['departure_stop'].queryset = stops_qs
                self.fields['arrival_stop'].queryset = stops_qs
            except Departure.DoesNotExist:
                pass
        else:
            self.fields['departure_stop'].help_text = "Sélectionnez d'abord un départ pour charger la liste des villes."
            self.fields['arrival_stop'].help_text = "Sélectionnez d'abord un départ pour charger la liste des villes."

    def clean(self):
        cleaned_data = super().clean()
        user = cleaned_data.get('user')
        departure = cleaned_data.get('departure')
        seats = cleaned_data.get('seats_reserved') or 1

        # 1. Auto-remplissage des données voyageur depuis le compte utilisateur
        if user:
            if not cleaned_data.get('traveler_name'):
                full_name = f"{user.first_name} {user.last_name}".strip()
                cleaned_data['traveler_name'] = full_name if full_name else user.username
            if not cleaned_data.get('traveler_phone') and user.phone:
                cleaned_data['traveler_phone'] = user.phone
            if not cleaned_data.get('traveler_email') and user.email:
                cleaned_data['traveler_email'] = user.email
            if (not cleaned_data.get('id_type') or cleaned_data.get('id_type') in ('none', 'OTHER', 'other')) and getattr(user, 'id_type', None):
                cleaned_data['id_type'] = normalize_id_type(user.id_type)
            if not cleaned_data.get('id_number') and getattr(user, 'id_number', None):
                cleaned_data['id_number'] = user.id_number

        # 2. Vérification et sélection automatique des arrêts si manquants
        if departure:
            start_stop = cleaned_data.get('departure_stop')
            end_stop = cleaned_data.get('arrival_stop')

            line_stops = departure.line.stops.select_related('city').order_by('stop_order')
            if not start_stop and line_stops.exists():
                start_stop = line_stops.first()
                cleaned_data['departure_stop'] = start_stop
            if not end_stop and line_stops.exists():
                end_stop = line_stops.last()
                cleaned_data['arrival_stop'] = end_stop

            # 3. Calcul automatique du tarif officiel
            pricing = departure.calculate_pricing(start_stop, end_stop)
            agency_unit = pricing['agency_price']
            platform_unit = pricing['platform_fee']
            total_unit = pricing['total_price']

            cleaned_data['agency_amount'] = agency_unit * seats
            cleaned_data['platform_fee'] = platform_unit * seats
            if not cleaned_data.get('total_amount') or cleaned_data.get('total_amount') <= 0:
                cleaned_data['total_amount'] = total_unit * seats

        if not cleaned_data.get('traveler_name'):
            cleaned_data['traveler_name'] = "Passager Inconnu"
        if not cleaned_data.get('traveler_phone'):
            cleaned_data['traveler_phone'] = "699000000"
        if not cleaned_data.get('id_type'):
            cleaned_data['id_type'] = 'CNI'
        else:
            cleaned_data['id_type'] = normalize_id_type(cleaned_data['id_type'])

        return cleaned_data


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    form = BookingAdminForm
    list_display = ('reference', 'traveler_name', 'traveler_phone', 'departure', 'seats_reserved', 'total_amount', 'status', 'payment_status', 'created_at')
    list_filter = ('status', 'payment_status', 'id_type', 'departure__agency', 'created_at')
    list_select_related = ('departure', 'departure__agency', 'user', 'departure_stop', 'arrival_stop')
    autocomplete_fields = ('user', 'departure')
    search_fields = ('reference', 'traveler_name', 'traveler_phone', 'id_number', 'traveler_email')
    
    # Rendre la référence et les logs de paiement impossibles à modifier
    readonly_fields = ('reference', 'created_at', 'transaction_id', 'paid_at')

    fieldsets = (
        ('1. Départ & Villes du Voyage', {
            'description': 'Sélectionnez le départ du car. Les villes de départ et d\'arrivée se chargent automatiquement et le tarif officiel est instantanément calculé.',
            'fields': ('departure', 'departure_stop', 'arrival_stop', 'seats_reserved')
        }),
        ('2. Client & Informations Voyageur', {
            'description': 'Recherchez simplement un compte utilisateur pour charger automatiquement toutes ses données (Nom, Téléphone, Email, CNI).',
            'fields': ('user', 'traveler_name', 'traveler_phone', 'traveler_email', 'id_type', 'id_number')
        }),
        ('3. Tarifs & Encaissement', {
            'fields': ('agency_amount', 'platform_fee', 'total_amount', 'status', 'payment_status', 'payment_method', 'transaction_id', 'reference')
        }),
        ('4. Dates & Horodatage', {
            'fields': ('created_at', 'paid_at'),
            'classes': ('collapse',)
        }),
    )

    class Media:
        js = ('bookings/js/admin_booking.js',)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('api/user-info/<int:user_id>/', self.admin_site.admin_view(self.api_user_info), name='booking_api_user_info'),
            path('api/departure-info/<int:departure_id>/', self.admin_site.admin_view(self.api_departure_info), name='booking_api_departure_info'),
            path('api/calculate-price/', self.admin_site.admin_view(self.api_calculate_price), name='booking_api_calculate_price'),
        ]
        return custom_urls + urls

    def api_user_info(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        full_name = f"{user.first_name} {user.last_name}".strip() or user.username
        mapped_id_type = normalize_id_type(user.id_type)
        return JsonResponse({
            'status': 'success',
            'traveler_name': full_name,
            'traveler_phone': user.phone or '',
            'traveler_email': user.email or '',
            'id_type': mapped_id_type,
            'id_number': user.id_number or '',
        })

    def api_departure_info(self, request, departure_id):
        dep = get_object_or_404(Departure.objects.select_related('line', 'agency'), pk=departure_id)
        stops = dep.line.stops.select_related('city').order_by('stop_order')
        stops_data = [
            {
                'id': s.id,
                'city_name': s.city.name,
                'order': s.stop_order,
                'price': float(s.price_from_start),
            }
            for s in stops
        ]
        pricing = dep.calculate_pricing()
        return JsonResponse({
            'status': 'success',
            'departure_id': dep.id,
            'agency_name': dep.agency.name,
            'line_name': dep.line.name,
            'available_capacity': dep.available_capacity,
            'stops': stops_data,
            'default_agency_price': pricing['agency_price'],
            'default_platform_fee': pricing['platform_fee'],
            'default_total_price': pricing['total_price'],
        })

    def api_calculate_price(self, request):
        departure_id = request.GET.get('departure_id')
        start_id = request.GET.get('start_id')
        end_id = request.GET.get('end_id')
        try:
            seats = max(1, int(request.GET.get('seats', 1) or 1))
        except (ValueError, TypeError):
            seats = 1

        if not departure_id or not str(departure_id).isdigit():
            return JsonResponse({'status': 'error', 'message': 'departure_id requis'}, status=400)

        dep = get_object_or_404(Departure.objects.select_related('line', 'agency'), pk=int(departure_id))
        start_stop = dep.line.stops.filter(pk=int(start_id)).first() if start_id and str(start_id).isdigit() else None
        end_stop = dep.line.stops.filter(pk=int(end_id)).first() if end_id and str(end_id).isdigit() else None

        pricing = dep.calculate_pricing(start_stop, end_stop)
        agency_amount = pricing['agency_price'] * seats
        platform_fee = pricing['platform_fee'] * seats
        total_amount = pricing['total_price'] * seats

        return JsonResponse({
            'status': 'success',
            'agency_amount': round(agency_amount, 2),
            'platform_fee': round(platform_fee, 2),
            'total_amount': round(total_amount, 2),
            'unit_price': round(pricing['total_price'], 2),
        })

    def save_model(self, request, obj, form, change):
        # 1. Synchronisation supplémentaire depuis User si nécessaire
        if obj.user:
            if not obj.traveler_name:
                obj.traveler_name = f"{obj.user.first_name} {obj.user.last_name}".strip() or obj.user.username
            if not obj.traveler_phone and obj.user.phone:
                obj.traveler_phone = obj.user.phone
            if not obj.traveler_email and obj.user.email:
                obj.traveler_email = obj.user.email
            if (not obj.id_type or obj.id_type in ('none', 'OTHER', 'other')) and getattr(obj.user, 'id_type', None):
                obj.id_type = normalize_id_type(obj.user.id_type)
            if not obj.id_number and getattr(obj.user, 'id_number', None):
                obj.id_number = obj.user.id_number

        # 2. Ajustement des arrêts et calcul financier automatique
        if obj.departure:
            line_stops = obj.departure.line.stops.select_related('city').order_by('stop_order')
            if not obj.departure_stop and line_stops.exists():
                obj.departure_stop = line_stops.first()
            if not obj.arrival_stop and line_stops.exists():
                obj.arrival_stop = line_stops.last()

            pricing = obj.departure.calculate_pricing(obj.departure_stop, obj.arrival_stop)
            seats = obj.seats_reserved or 1
            if not obj.agency_amount or obj.agency_amount <= 0:
                obj.agency_amount = pricing['agency_price'] * seats
            if not obj.platform_fee or obj.platform_fee <= 0:
                obj.platform_fee = pricing['platform_fee'] * seats
            if not obj.total_amount or obj.total_amount <= 0:
                obj.total_amount = pricing['total_price'] * seats

            # Mise à jour de la capacité disponible du départ lors d'une nouvelle réservation
            if not change and obj.departure.available_capacity >= seats:
                obj.departure.available_capacity -= seats
                obj.departure.save(update_fields=['available_capacity'])

        super().save_model(request, obj, form, change)
    
    actions = ['send_reminders', 'send_survey', 'cancel_and_refund', 'send_ticket_email']

    @admin.action(description="Envoyer l'e-mail de confirmation (Billet) au(x) passager(s)")
    def send_ticket_email(self, request, queryset):
        from accounts.services.email_service import send_ticket_confirmation_email
        count_success = 0
        skipped_boarded = 0
        skipped_expired = 0
        skipped_not_paid = 0
        skipped_cancelled = 0
        skipped_no_email = 0
        count_failed = 0
        
        for booking in queryset:
            can_send, reason = booking.can_send_ticket_email
            if not can_send:
                if booking.status == 'boarded':
                    skipped_boarded += 1
                elif booking.is_expired:
                    skipped_expired += 1
                elif booking.status == 'cancelled':
                    skipped_cancelled += 1
                elif booking.payment_status != 'paid':
                    skipped_not_paid += 1
                else:
                    skipped_no_email += 1
                continue
                
            if send_ticket_confirmation_email(request, booking):
                count_success += 1
            else:
                count_failed += 1
                
        if count_success > 0:
            self.message_user(request, f"{count_success} e-mail(s) de billet(s) envoyé(s) avec succès.", level='SUCCESS')
            
        reasons = []
        if skipped_boarded:
            reasons.append(f"{skipped_boarded} déjà validé(s)/embarqué(s)")
        if skipped_expired:
            reasons.append(f"{skipped_expired} expiré(s) (date de voyage passée)")
        if skipped_cancelled:
            reasons.append(f"{skipped_cancelled} annulé(s)")
        if skipped_not_paid:
            reasons.append(f"{skipped_not_paid} non payé(s)")
        if skipped_no_email:
            reasons.append(f"{skipped_no_email} sans adresse email")
        if count_failed:
            reasons.append(f"{count_failed} échec(s) technique(s) d'envoi")
            
        if reasons:
            self.message_user(request, f"Réservations ignorées : {', '.join(reasons)}.", level='WARNING')

    @admin.action(description="Annuler la réservation et créditer le portefeuille du client")
    def cancel_and_refund(self, request, queryset):
        from django.db import transaction
        from accounts.services.email_service import send_cancellation_email
        count_success = 0
        skipped_boarded = 0
        skipped_expired = 0
        skipped_already_cancelled = 0
        count_failed = 0
        
        for booking in queryset:
            if booking.status == 'cancelled':
                skipped_already_cancelled += 1
                continue
            if booking.status == 'boarded':
                skipped_boarded += 1
                continue
            if booking.is_expired:
                skipped_expired += 1
                continue
                
            with transaction.atomic():
                was_paid = (booking.payment_status == 'paid')
                if booking.cancel():
                    # Créditer le portefeuille UNIQUEMENT si la réservation a été effectivement payée
                    if was_paid and hasattr(booking.user, 'wallet_balance') and booking.user:
                        booking.user.wallet_balance += booking.total_amount
                        booking.user.save(update_fields=['wallet_balance'])
                        booking.payment_status = 'refunded'
                    else:
                        booking.payment_status = 'failed' if booking.payment_status == 'pending' else booking.payment_status
                    booking.save(update_fields=['payment_status'])
                    send_cancellation_email(booking)
                    count_success += 1
                else:
                    count_failed += 1
                
        if count_success > 0:
            self.message_user(request, f"{count_success} réservation(s) annulée(s) (remboursement appliqué pour les réservations payées).", level='SUCCESS')
            
        reasons = []
        if skipped_boarded:
            reasons.append(f"{skipped_boarded} refusée(s) car déjà validée(s)/embarquée(s)")
        if skipped_expired:
            reasons.append(f"{skipped_expired} refusée(s) car date de voyage expirée")
        if skipped_already_cancelled:
            reasons.append(f"{skipped_already_cancelled} déjà annulée(s)")
        if count_failed:
            reasons.append(f"{count_failed} échec(s)")
            
        if reasons:
            self.message_user(request, f"Réservations non annulées : {', '.join(reasons)}.", level='WARNING')

    @admin.action(description="Envoyer un e-mail de rappel aux passagers sélectionnés")
    def send_reminders(self, request, queryset):
        from accounts.services.email_service import send_trip_reminder_email
        count_success = 0
        skipped = 0
        count_failed = 0
        
        for booking in queryset:
            if not booking.traveler_email or booking.status != 'confirmed' or booking.is_expired:
                skipped += 1
                continue
            if send_trip_reminder_email(booking):
                count_success += 1
            else:
                count_failed += 1
                
        if count_success > 0:
            self.message_user(request, f"{count_success} rappel(s) envoyé(s) avec succès.", level='SUCCESS')
        if skipped > 0 or count_failed > 0:
            self.message_user(request, f"{skipped} réservation(s) ignorée(s) (voyage passé/expiré, passager déjà validé/embarqué, ou sans e-mail).", level='WARNING')
            
    @admin.action(description="Envoyer une enquête de satisfaction aux passagers sélectionnés")
    def send_survey(self, request, queryset):
        from accounts.services.email_service import send_satisfaction_survey_email
        count_success = 0
        count_failed = 0
        
        for booking in queryset:
            # On envoie seulement à ceux qui ont une adresse email et dont le billet a été validé ou embarqué
            if booking.traveler_email and booking.status in ['confirmed', 'boarded']:
                if send_satisfaction_survey_email(request, booking):
                    count_success += 1
                else:
                    count_failed += 1
            else:
                count_failed += 1
                
        if count_success > 0:
            self.message_user(request, f"{count_success} enquête(s) de satisfaction envoyée(s).", level='SUCCESS')
        if count_failed > 0:
            self.message_user(request, f"{count_failed} réservation(s) ignorée(s) (pas d'e-mail ou statut invalide).", level='WARNING')

