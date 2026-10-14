from datetime import timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.urls import reverse_lazy, reverse
from django.db.models import Sum, Q
from django.utils import timezone
from .models import Agency, AuditAnomaly, AgencyNotification
from .context_processors import get_current_agency
from travel.models import Departure, Vehicle, Line
from bookings.models import Booking
from .forms import VehicleForm, DepartureForm, AgencySettingsForm, AgencyBookingForm
from django.contrib.auth.views import LoginView

def is_manager(user):
    return user.is_authenticated and user.role in ['admin', 'manager']

def manager_required(view_func):
    return user_passes_test(is_manager, login_url='agencies:login')(view_func)

class ManagerRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_manager(self.request.user)
    
    def handle_no_permission(self):
        messages.error(self.request, "Accès refusé. Seul le chef d'agence peut effectuer cette action.")
        return redirect('agencies:dashboard')

@login_required(login_url='agencies:login')
def switch_agency(request, agency_id):
    """
    Cloisonnement strict : seuls les super-administrateurs globaux de la plateforme
    peuvent auditer différentes agences. Les utilisateurs et agents d'agence sont
    strictement cantonnés à leur propre entreprise sans aucune permutation possible.
    """
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Accès refusé : votre compte est strictement restreint au périmètre de votre agence.")
        return redirect('agencies:dashboard')
        
    agency = get_object_or_404(Agency, id=agency_id)
    request.session['current_agency_id'] = agency.id
    messages.info(request, f"Espace d'administration : {agency.name}")
    next_url = request.META.get('HTTP_REFERER') or reverse('agencies:dashboard')
    if any(k in next_url for k in ['/edit/', '/delete/', '/annuler/', '/manifeste/', '/passagers/']):
        next_url = reverse('agencies:dashboard')
    return redirect(next_url)


@login_required(login_url='agencies:login')
def dashboard(request):
    agency = get_current_agency(request)
    
    stats = {}
    upcoming_departures = []
    recent_bookings = []
    
    period = request.GET.get('period', 'month').lower()
    now = timezone.localtime()
    today = now.date()
    current_time = now.time()
    
    # 1. Détermination de la période et des bornes temporelles
    if period == 'today':
        period_label = "Aujourd'hui"
        period_date_range = today.strftime("%d/%m/%Y")
    elif period == 'week':
        start_week = today - timedelta(days=6)
        period_label = "7 derniers jours"
        period_date_range = f"Du {start_week.strftime('%d/%m')} au {today.strftime('%d/%m/%Y')}"
    elif period == 'year':
        period_label = f"Année {today.year}"
        period_date_range = f"01/01/{today.year} - 31/12/{today.year}"
    elif period == 'all':
        period_label = "Historique global"
        period_date_range = "Toutes les dates enregistrées"
    else: # 'month' par défaut
        period = 'month'
        month_names = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
        month_name = month_names[today.month - 1]
        period_label = f"Ce mois-ci ({month_name} {today.year})"
        period_date_range = f"Du 01/{today.month:02d}/{today.year} au {today.strftime('%d/%m/%Y')}"

    if agency:
        departures = Departure.objects.filter(agency=agency)
        bookings = Booking.objects.filter(departure__agency=agency)
        
        # Filtrage selon la période sélectionnée
        if period == 'today':
            period_bookings = bookings.filter(created_at__date=today)
        elif period == 'week':
            start_week = today - timedelta(days=6)
            period_bookings = bookings.filter(created_at__date__gte=start_week, created_at__date__lte=today)
        elif period == 'year':
            period_bookings = bookings.filter(created_at__year=today.year)
        elif period == 'all':
            period_bookings = bookings
        else: # month
            period_bookings = bookings.filter(created_at__year=today.year, created_at__month=today.month)

        # 2. Ventes Totales (Engagements de voyage non annulés)
        valid_bookings = period_bookings.exclude(status='cancelled')
        stats['total_sales'] = valid_bookings.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        stats['tickets_sold'] = valid_bookings.aggregate(Sum('seats_reserved'))['seats_reserved__sum'] or 0
        stats['total_bookings_count'] = valid_bookings.count()

        # 3. Recettes Encaissées Réelles (Paiements effectifs perçus)
        paid_bookings = valid_bookings.filter(payment_status='paid')
        stats['total_revenue'] = paid_bookings.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        stats['paid_bookings_count'] = paid_bookings.count()
        stats['paid_seats_count'] = paid_bookings.aggregate(Sum('seats_reserved'))['seats_reserved__sum'] or 0

        # 4. En Attente d'Encaissement (Créances passager à régler au guichet)
        pending_payment_bookings = valid_bookings.filter(payment_status='pending')
        stats['pending_amount'] = pending_payment_bookings.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        stats['pending_bookings_count'] = pending_payment_bookings.count()

        # Taux d'encaissement (%)
        if stats['total_sales'] > 0:
            stats['collection_rate'] = round((float(stats['total_revenue']) / float(stats['total_sales'])) * 100, 1)
        else:
            stats['collection_rate'] = 100.0

        # 5. Départs Actifs, Remplissage & Statistiques Opérationnelles Précises
        # Un départ actif est un départ FUTUR ou EN EMBARQUEMENT AUJOURD'HUI
        future_or_current_deps = departures.filter(
            Q(date__gt=today) | Q(date=today, time__gt=current_time),
            status__in=['scheduled', 'boarding']
        )
        
        # Découpage explicite par temporalité
        today_deps = departures.filter(date=today)
        stats['today_total'] = today_deps.count()
        stats['today_boarding'] = today_deps.filter(status='boarding').count()
        stats['today_upcoming'] = today_deps.filter(status='scheduled', time__gt=current_time).count()
        stats['today_departed'] = today_deps.filter(Q(status='departed') | Q(time__lte=current_time)).count()
        
        stats['active_departures'] = future_or_current_deps.count()
        stats['total_available_seats'] = future_or_current_deps.aggregate(Sum('available_capacity'))['available_capacity__sum'] or 0
        stats['historical_total'] = departures.count()

        # Prochains départs : UNIQUEMENT les départs à venir NON clôturés et avec places disponibles
        upcoming_departures = future_or_current_deps.filter(
            available_capacity__gt=0
        ).select_related('line', 'vehicle', 'driver').order_by('date', 'time')[:6]

        recent_bookings = bookings.select_related(
            'departure', 'departure__line', 'departure__vehicle',
            'departure_stop__city', 'arrival_stop__city'
        ).order_by('-created_at')[:7]

    context = {
        'current_agency': agency,
        'stats': stats,
        'upcoming_departures': upcoming_departures,
        'recent_bookings': recent_bookings,
        'current_period': period,
        'period_label': period_label,
        'period_date_range': period_date_range,
        'last_updated': now,
    }
    return render(request, 'agencies/dashboard.html', context)

class AgencyLoginView(LoginView):
    template_name = 'agencies/login.html'
    
    def get_success_url(self):
        return reverse_lazy('agencies:dashboard')
        
    def get(self, request, *args, **kwargs):
        if request.session.pop('kicked_out', False):
            messages.warning(request, "Votre session a été fermée car ce compte s'est connecté depuis un autre appareil.")
        return super().get(request, *args, **kwargs)

class AgencyMixin:
    """Mixin to restrict querysets to the current user's active agency"""
    login_url = reverse_lazy('agencies:login')

    def get_login_url(self):
        return str(reverse_lazy('agencies:login'))
    
    def get_agency(self):
        return get_current_agency(self.request)

    def get_queryset(self):
        agency = self.get_agency()
        return super().get_queryset().filter(agency=agency)


class VehicleListView(AgencyMixin, LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = 'agencies/vehicle_list.html'
    context_object_name = 'vehicles'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        context['total_vehicles'] = qs.count()
        context['available_count'] = qs.filter(status='available').count()
        context['in_trip_count'] = qs.filter(status='in_trip').count()
        context['maintenance_count'] = qs.filter(status__in=['maintenance', 'out_of_service']).count()
        return context

class VehicleCreateView(AgencyMixin, ManagerRequiredMixin, CreateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = 'agencies/vehicle_form.html'
    success_url = reverse_lazy('agencies:vehicle_list')

    def form_valid(self, form):
        form.instance.agency = get_current_agency(self.request)
        return super().form_valid(form)

class VehicleUpdateView(AgencyMixin, ManagerRequiredMixin, UpdateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = 'agencies/vehicle_form.html'
    success_url = reverse_lazy('agencies:vehicle_list')

class VehicleDeleteView(AgencyMixin, ManagerRequiredMixin, DeleteView):
    model = Vehicle
    template_name = 'agencies/vehicle_confirm_delete.html'
    success_url = reverse_lazy('agencies:vehicle_list')

class DepartureListView(AgencyMixin, LoginRequiredMixin, ListView):
    model = Departure
    template_name = 'agencies/departure_list.html'
    context_object_name = 'departures'
    paginate_by = 25

    def get_queryset(self):
        agency = get_current_agency(self.request)
        now = timezone.localtime()
        today = now.date()
        current_time = now.time()
        time_filter = self.request.GET.get('time_filter', 'upcoming').lower()

        qs = Departure.objects.filter(agency=agency).select_related('line', 'vehicle', 'driver')

        # Priorité 1 : Tri chronologique & Filtrage temporel
        if time_filter == 'today':
            qs = qs.filter(date=today).order_by('time')
        elif time_filter == 'past':
            qs = qs.filter(
                Q(date__lt=today) | Q(date=today, time__lte=current_time) | Q(status__in=['departed', 'cancelled'])
            ).order_by('-date', '-time')
        elif time_filter == 'all':
            qs = qs.order_by('-date', 'time')
        else: # 'upcoming' par défaut : voyages à partir d'aujourd'hui en ordre chronologique
            time_filter = 'upcoming'
            qs = qs.filter(
                Q(date__gt=today) | Q(date=today, time__gt=current_time)
            ).exclude(status__in=['departed', 'cancelled']).order_by('date', 'time')

        # Recherche textuelle étendue (Ligne, Immatriculation, Bus, Chauffeur)
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(line__name__icontains=q) |
                Q(vehicle__registration__icontains=q) |
                Q(vehicle__vehicle_type__icontains=q) |
                Q(driver__first_name__icontains=q) |
                Q(driver__last_name__icontains=q)
            )

        status = self.request.GET.get('status', '').strip()
        if status in ['scheduled', 'boarding', 'departed', 'cancelled']:
            qs = qs.filter(status=status)

        date = self.request.GET.get('date', '').strip()
        if date:
            qs = qs.filter(date=date)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agency = get_current_agency(self.request)
        now = timezone.localtime()
        today = now.date()
        current_time = now.time()
        time_filter = self.request.GET.get('time_filter', 'upcoming').lower()

        context['q'] = self.request.GET.get('q', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['selected_date'] = self.request.GET.get('date', '')
        context['time_filter'] = time_filter
        context['today'] = today

        # Compteurs rapides pour les onglets
        if agency:
            all_deps = Departure.objects.filter(agency=agency)
            context['count_upcoming'] = all_deps.filter(
                Q(date__gt=today) | Q(date=today, time__gt=current_time)
            ).exclude(status__in=['departed', 'cancelled']).count()
            context['count_today'] = all_deps.filter(date=today).count()
            context['count_past'] = all_deps.filter(
                Q(date__lt=today) | Q(date=today, time__lte=current_time) | Q(status__in=['departed', 'cancelled'])
            ).count()
            context['count_all'] = all_deps.count()

        return context

class DepartureCreateView(AgencyMixin, ManagerRequiredMixin, CreateView):
    model = Departure
    form_class = DepartureForm
    template_name = 'agencies/departure_form.html'
    success_url = reverse_lazy('agencies:departure_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['agency'] = get_current_agency(self.request)
        return kwargs

    def form_valid(self, form):
        form.instance.agency = get_current_agency(self.request)
        if form.instance.vehicle:
            form.instance.available_capacity = form.instance.vehicle.capacity
        messages.success(self.request, f"Le départ '{form.instance.line.name}' a été programmé avec succès !")
        return super().form_valid(form)

class DepartureUpdateView(AgencyMixin, ManagerRequiredMixin, UpdateView):
    model = Departure
    form_class = DepartureForm
    template_name = 'agencies/departure_form.html'
    success_url = reverse_lazy('agencies:departure_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['agency'] = get_current_agency(self.request)
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, f"Le départ '{form.instance.line.name}' a été mis à jour avec succès !")
        return super().form_valid(form)

class DepartureDeleteView(AgencyMixin, ManagerRequiredMixin, DeleteView):
    model = Departure
    template_name = 'agencies/departure_confirm_delete.html'
    success_url = reverse_lazy('agencies:departure_list')

    def dispatch(self, request, *args, **kwargs):
        departure = self.get_object()
        # Si le départ compte des réservations actives, interdire la suppression définitive et rediriger vers l'annulation
        if departure.bookings.exclude(status='cancelled').exists():
            passenger_count = sum(b.seats_reserved for b in departure.bookings.exclude(status='cancelled'))
            messages.warning(
                request,
                f"Ce départ compte {passenger_count} passager(s) réservé(s). "
                "Pour conserver l'historique comptable et légal, vous devez l'Annuler au lieu de le supprimer."
            )
            return redirect('agencies:departure_cancel', pk=departure.pk)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Le départ vide a été supprimé définitivement.")
        return super().form_valid(form)

@manager_required
def departure_cancel(request, pk):
    """
    Annulation officielle d'un départ avec passagers enregistrés.
    Conserve l'historique, prévient la perte de données et met à jour les réservations associées.
    """
    agency = get_current_agency(request)
    departure = get_object_or_404(
        Departure.objects.select_related('line', 'vehicle', 'driver'),
        id=pk,
        agency=agency
    )
    
    valid_bookings = departure.bookings.exclude(status='cancelled').select_related(
        'departure_stop__city', 'arrival_stop__city'
    )
    passenger_count = sum(b.seats_reserved for b in valid_bookings)
    booking_count = valid_bookings.count()

    if request.method == 'POST':
        # Annulation officielle du départ
        departure.status = 'cancelled'
        departure.save(update_fields=['status'])
        
        # Mettre à jour les réservations pour refléter l'annulation
        valid_bookings.update(status='cancelled')
        
        messages.success(
            request,
            f"Le départ '{departure.line.name}' du {departure.date.strftime('%d/%m/%Y')} à {departure.time.strftime('%H:%M')} "
            f"a été officiellement annulé. Les {passenger_count} passager(s) ({booking_count} réservations) ont été archivés."
        )
        return redirect('agencies:departure_list')

    return render(request, 'agencies/departure_cancel.html', {
        'departure': departure,
        'valid_bookings': valid_bookings,
        'passenger_count': passenger_count,
        'booking_count': booking_count,
    })

class BookingListView(AgencyMixin, LoginRequiredMixin, ListView):
    model = Booking
    template_name = 'agencies/booking_list.html'
    context_object_name = 'bookings'
    login_url = reverse_lazy('agencies:login')
    paginate_by = 30

    def get_queryset(self):
        agency = get_current_agency(self.request)
        qs = Booking.objects.filter(departure__agency=agency).select_related(
            'departure', 'departure__vehicle', 'departure__line',
            'departure_stop__city', 'arrival_stop__city', 'user'
        ).order_by('-created_at')

        # Recherche par mot-clé (Nom, Téléphone, Référence, Numéro CNI, Email)
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(reference__icontains=q) |
                Q(traveler_name__icontains=q) |
                Q(traveler_phone__icontains=q) |
                Q(id_number__icontains=q) |
                Q(traveler_email__icontains=q)
            )

        # Filtre par statut réservation
        status = self.request.GET.get('status', '').strip()
        if status in ['confirmed', 'boarded', 'cancelled', 'pending']:
            qs = qs.filter(status=status)

        # Filtre par statut paiement / encaissement
        payment_status = self.request.GET.get('payment_status', '').strip()
        if payment_status in ['paid', 'pending', 'refunded', 'failed']:
            qs = qs.filter(payment_status=payment_status)

        # Filtre par départ spécifique
        dep_id = self.request.GET.get('departure', '').strip()
        if dep_id and dep_id.isdigit():
            qs = qs.filter(departure_id=int(dep_id))

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agency = get_current_agency(self.request)
        all_agency_bookings = Booking.objects.filter(departure__agency=agency)
        valid_bookings = all_agency_bookings.exclude(status='cancelled')

        context['q'] = self.request.GET.get('q', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['current_payment_status'] = self.request.GET.get('payment_status', '')
        context['selected_departure'] = self.request.GET.get('departure', '')
        
        # Statistiques claires : Ventes vs Encaissements vs En attente
        context['total_bookings_count'] = all_agency_bookings.count()
        context['confirmed_count'] = all_agency_bookings.filter(status='confirmed').count()
        context['boarded_count'] = all_agency_bookings.filter(status='boarded').count()
        context['cancelled_count'] = all_agency_bookings.filter(status='cancelled').count()

        # Volume total des ventes (non annulées)
        context['total_sales'] = valid_bookings.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        # Recettes réellement encaissées
        context['total_revenue'] = valid_bookings.filter(payment_status='paid').aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        context['paid_count'] = valid_bookings.filter(payment_status='paid').count()
        # Montant restant à encaisser au guichet
        context['pending_revenue'] = valid_bookings.filter(payment_status='pending').aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        context['pending_payment_count'] = valid_bookings.filter(payment_status='pending').count()

        # Liste des départs récents de l'agence pour le filtre déroulant
        context['recent_departures'] = Departure.objects.filter(agency=agency).select_related('line', 'vehicle').order_by('-date', '-time')[:25]
        return context


from django.http import JsonResponse
import re

@login_required(login_url='agencies:login')
@require_POST
def ajax_scan_ticket(request):
    """
    Endpoint AJAX sécurisé pour scanner un billet et valider l'embarquement.
    Gère :
    1. Les scans de QR Code URL (/verifier/<reference>/?sig=<signature>)
    2. Les scans legacy (REF:...|SIG:...)
    3. Les saisies manuelles au clavier ou douchette laser directe
    4. Vérification d'authenticité cryptographique de la signature
    5. BLOCAGE STRICT SI NON PAYÉ (payment_status != 'paid')
    6. Détection des doublons et annulations
    """
    agency = get_current_agency(request)
    if not agency:
        return JsonResponse({'status': 'error', 'message': "Session d'agence non trouvée ou expirée."}, status=403)

    payload = request.POST.get('payload', '').strip()
    if not payload:
        return JsonResponse({'status': 'error', 'message': "Aucun contenu scanné ou référence reçu."}, status=400)
    
    reference = None
    sig = None

    # 1. Format URL officielle : /verifier/<reference>/?sig=<sig>
    url_match = re.search(r'/verifier/([a-zA-Z0-9\-]+)', payload)
    if url_match:
        reference = url_match.group(1).upper()
        sig_match = re.search(r'[?&]sig=([a-zA-Z0-9]+)', payload)
        if sig_match:
            sig = sig_match.group(1)

    # 2. Format legacy texte : REF:<reference>|...|SIG:<sig>
    if not reference:
        ref_match = re.search(r'REF:([a-zA-Z0-9\-]+)', payload)
        if ref_match:
            reference = ref_match.group(1).upper()
            sig_match = re.search(r'SIG:([a-zA-Z0-9]+)', payload)
            if sig_match:
                sig = sig_match.group(1)

    # 3. Format direct ou saisie clavier : ET-XXXXXXXX
    if not reference:
        direct_match = re.search(r'\b(ET-[A-Z0-9\-]+)\b', payload, re.IGNORECASE)
        if direct_match:
            reference = direct_match.group(1).upper()
        else:
            reference = payload.strip().upper()

    try:
        booking = Booking.objects.select_related('departure', 'departure__agency').get(reference=reference)
    except Booking.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': f"Billet introuvable pour la référence '{reference}'."}, status=404)
        
    # Vérification d'appartenance à l'agence connectée
    if booking.departure.agency != agency:
        return JsonResponse({
            'status': 'error', 
            'message': f"Ce billet appartient à la compagnie '{booking.departure.agency.name}' et non à votre agence."
        }, status=403)

    # Contrôle de signature cryptographique si fournie par le QR code
    if sig:
        if not booking.verify_security_signature(sig):
            return JsonResponse({
                'status': 'error', 
                'message': "ÉCHEC SÉCURITÉ : Signature du QR Code non authentique (billet contrefait ou altéré) !"
            }, status=400)

    # FAILLE CRITIQUE CORRIGÉE : BLOCAGE STRICT DE L'EMBARQUEMENT SI NON PAYÉ
    if booking.payment_status != 'paid':
        return JsonResponse({
            'status': 'error', 
            'message': f"EMBARQUEMENT REFUSÉ : Le billet {booking.reference} n'est PAS réglé ({booking.get_payment_status_display()}). Veuillez encaisser le voyageur au guichet."
        }, status=400)

    # Contrôle de statut de la réservation
    if booking.status == 'cancelled':
        return JsonResponse({
            'status': 'error', 
            'message': f"EMBARQUEMENT REFUSÉ : Le billet {booking.reference} a été ANNULÉ."
        }, status=400)
        
    if booking.status == 'boarded':
        return JsonResponse({
            'status': 'warning', 
            'message': f"ATTENTION : Le passager {booking.traveler_name} a DÉJÀ embarqué !"
        }, status=200)

    if booking.is_expired and booking.departure.status in ['departed', 'cancelled']:
        return JsonResponse({
            'status': 'error',
            'message': "Ce départ est clôturé ou le car est déjà parti."
        }, status=400)
        
    # Validation de l'embarquement
    booking.status = 'boarded'
    booking.save(update_fields=['status'])
    
    seats_text = f"{booking.seats_reserved} place{'s' if booking.seats_reserved > 1 else ''}"
    return JsonResponse({
        'status': 'success', 
        'message': f"✓ VALIDÉ : {booking.traveler_name} ({seats_text}) embarqué(e) avec succès !",
        'booking_id': booking.id
    })

@login_required(login_url='agencies:login')
@require_POST
def update_booking_status(request, booking_id):
    """Permet aux agents d'enregistrer l'embarquement, d'encaisser au guichet ou d'annuler une réservation"""
    agency = get_current_agency(request)
    booking = get_object_or_404(Booking, id=booking_id, departure__agency=agency)
    
    new_status = request.POST.get('status')
    new_payment_status = request.POST.get('payment_status')

    # 1. Mise à jour du statut de réservation
    if new_status in ['confirmed', 'boarded', 'cancelled']:
        if new_status == 'cancelled':
            booking.cancel()
        else:
            booking.status = new_status
            booking.save(update_fields=['status'])
        messages.success(request, f"Statut de la réservation {booking.reference} mis à jour : {booking.get_status_display()}.")

    # 2. Mise à jour du statut d'encaissement / paiement
    if new_payment_status in ['paid', 'pending', 'refunded', 'failed']:
        booking.payment_status = new_payment_status
        if new_payment_status == 'paid' and not booking.paid_at:
            booking.paid_at = timezone.now()
        booking.save(update_fields=['payment_status', 'paid_at'])
        messages.success(request, f"Statut de paiement de {booking.reference} mis à jour : {booking.get_payment_status_display()}.")

    next_url = request.POST.get('next') or reverse('agencies:booking_list')
    return redirect(next_url)


@login_required(login_url='agencies:login')
def agency_booking_create(request):
    """
    Action Rapide : Vente au Guichet & Nouvelle Réservation.
    Permet à l'agent de guichet d'enregistrer un passager, d'émettre son billet et d'encaisser en direct.
    """
    agency = get_current_agency(request)
    if not agency:
        messages.error(request, "Aucune agence associée à votre compte.")
        return redirect('agencies:dashboard')

    departure_id = request.GET.get('departure_id') or request.POST.get('departure')
    selected_departure = None
    if departure_id and str(departure_id).isdigit():
        selected_departure = Departure.objects.filter(id=int(departure_id), agency=agency).first()
        if selected_departure and selected_departure.is_closed_for_sale():
            messages.warning(request, f"Le départ '{selected_departure.line.name}' est clôturé à la vente (horaire passé ou départ parti).")
            selected_departure = None

    if request.method == 'POST':
        form = AgencyBookingForm(request.POST, agency=agency, departure=selected_departure)
        if form.is_valid():
            booking = form.save(commit=False)
            departure = booking.departure
            
            # Calcul du prix du tronçon
            start_stop = booking.departure_stop
            end_stop = booking.arrival_stop
            unit_price = max(0, end_stop.price_from_start - start_stop.price_from_start)
            
            # Au guichet, pas de frais de plateforme
            booking.agency_amount = unit_price * booking.seats_reserved
            booking.platform_fee = 0
            booking.total_amount = booking.agency_amount
            
            booking.status = 'confirmed'
            booking.user = request.user
            
            if booking.payment_status == 'paid':
                booking.paid_at = timezone.now()
                
            booking.save()
            
            # Envoi de l'email de confirmation si l'adresse email est renseignée
            if booking.traveler_email:
                from accounts.services.email_service import send_ticket_confirmation_email
                send_ticket_confirmation_email(request, booking)
            
            # Décrémentation de la capacité
            departure.available_capacity = max(0, departure.available_capacity - booking.seats_reserved)
            departure.save(update_fields=['available_capacity'])
            
            encaisse_str = "Encaissé au guichet" if booking.payment_status == 'paid' else "En attente de règlement"
            messages.success(
                request, 
                f"Billet {booking.reference} émis avec succès pour {booking.traveler_name} ! "
                f"({booking.seats_reserved} place(s), {booking.total_amount:,.0f} FCFA - {encaisse_str}).".replace(',', ' ')
            )
            return redirect('bookings:confirmation', reference=booking.reference)
    else:
        form = AgencyBookingForm(agency=agency, departure=selected_departure)

    # Récupérer UNIQUEMENT les départs actifs et futurs avec leurs arrêts pour mise à jour dynamique JS
    now = timezone.localtime()
    today = now.date()
    current_time = now.time()
    active_departures = Departure.objects.filter(
        agency=agency, 
        status__in=['scheduled', 'boarding'],
        available_capacity__gt=0
    ).filter(
        Q(date__gt=today) | Q(date=today, time__gt=current_time)
    ).select_related('line', 'vehicle').prefetch_related('line__stops__city').order_by('date', 'time')

    return render(request, 'agencies/booking_create.html', {
        'form': form,
        'agency': agency,
        'selected_departure': selected_departure,
        'active_departures': active_departures,
    })


@login_required(login_url='agencies:login')
def departure_manifest(request, departure_id):
    """
    Liste des passagers, Consultation, Impression et Suivi de l'embarquement en temps réel.
    """
    agency = get_current_agency(request)
    departure = get_object_or_404(
        Departure.objects.select_related('agency', 'line', 'vehicle', 'driver'),
        id=departure_id,
        agency=agency
    )

    # 1. Action rapide de suivi d'embarquement (Check-in en direct)
    if request.method == 'POST' and request.POST.get('action') == 'toggle_boarded':
        booking_id = request.POST.get('booking_id')
        booking = get_object_or_404(Booking, id=booking_id, departure=departure)
        if booking.status == 'boarded':
            booking.status = 'confirmed'
            messages.info(request, f"Embarquement annulé pour {booking.traveler_name}.")
        else:
            booking.status = 'boarded'
            messages.success(request, f"Passager {booking.traveler_name} ({booking.seats_reserved} pl.) marqué comme embarqué !")
        booking.save(update_fields=['status'])
        return redirect('agencies:departure_manifest', departure_id=departure.id)

    # 2. Données et statistiques des passagers
    bookings = Booking.objects.filter(departure=departure).select_related(
        'departure_stop__city', 'arrival_stop__city'
    ).order_by('departure_stop__stop_order', 'traveler_name')

    valid_bookings = [b for b in bookings if b.status != 'cancelled']
    total_passengers = sum(b.seats_reserved for b in valid_bookings)
    boarded_passengers = sum(b.seats_reserved for b in valid_bookings if b.status == 'boarded')
    pending_passengers = total_passengers - boarded_passengers
    boarding_percentage = round((boarded_passengers / total_passengers * 100), 1) if total_passengers > 0 else 0

    # Trésorerie du départ
    paid_revenue = sum(b.total_amount for b in valid_bookings if b.payment_status == 'paid')
    pending_revenue = sum(b.total_amount for b in valid_bookings if b.payment_status == 'pending')

    return render(request, 'agencies/manifest.html', {
        'departure': departure,
        'bookings': bookings,
        'valid_bookings': valid_bookings,
        'total_passengers': total_passengers,
        'boarded_passengers': boarded_passengers,
        'pending_passengers': pending_passengers,
        'boarding_percentage': boarding_percentage,
        'paid_revenue': paid_revenue,
        'pending_revenue': pending_revenue,
    })


@manager_required
def agency_settings(request):
    """
    Paramètres complets de l'agence structurés selon les 5 modules métier :
    1. Préférences d'affichage et interface (Thème clair/sombre, charte, langue, formats)
    2. Profil & Informations légales de l'entreprise (Identité, coordonnées, mentions légales des billets)
    3. Paramètres opérationnels (Devise, TVA, fuseau horaire, délais de réservation/annulation)
    4. Matériel et périphériques (Format d'impression A4/thermique, lecteur QR code, balance)
    5. Notifications et automatisations (Passagers SMS/Email, alertes sonores agents)
    """
    agency = get_current_agency(request)
    if not agency:
        messages.error(request, "Aucune agence n'est associée à votre compte.")
        return redirect('agencies:dashboard')

    if request.method == 'POST':
        form = AgencySettingsForm(request.POST, request.FILES, instance=agency)
        if form.is_valid():
            form.save()
            messages.success(request, "Les paramètres de votre agence ont été mis à jour avec succès !")
            return redirect('agencies:settings')
    else:
        form = AgencySettingsForm(instance=agency)

    return render(request, 'agencies/settings.html', {
        'form': form,
        'current_agency': agency,
        'active_tab': request.GET.get('tab', 'display'),
    })


# -------------------------------------------------------------------------
# CENTRE DE TRAITEMENT DES ANOMALIES & AUDIT INTELLIGENT GEMINI
# -------------------------------------------------------------------------

@manager_required
def anomaly_list(request):
    """
    Centre de traitement des anomalies et alertes de fraude détectées par Gemini.
    Principe : 'Gemini signale, un humain tranche'.
    """
    agency = get_current_agency(request)
    if not agency:
        messages.error(request, "Aucune agence active.")
        return redirect('agencies:dashboard')

    qs = AuditAnomaly.objects.filter(agency=agency).select_related('booking', 'departure', 'cashier', 'resolved_by')

    # Filtrages
    status_filter = request.GET.get('status', 'pending')
    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)

    severity_filter = request.GET.get('severity', '')
    if severity_filter:
        qs = qs.filter(severity=severity_filter)

    category_filter = request.GET.get('category', '')
    if category_filter:
        qs = qs.filter(category=category_filter)

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(description__icontains=q) |
            Q(booking__reference__icontains=q) |
            Q(booking__traveler_name__icontains=q)
        )

    # Statistiques du centre de traitement
    all_anomalies = AuditAnomaly.objects.filter(agency=agency)
    stats = {
        'total': all_anomalies.count(),
        'pending': all_anomalies.filter(status='pending').count(),
        'critical': all_anomalies.filter(status='pending', severity='critical').count(),
        'confirmed': all_anomalies.filter(status='confirmed').count(),
        'dismissed': all_anomalies.filter(status='dismissed').count(),
        'regularized': all_anomalies.filter(status='regularized').count(),
    }

    return render(request, 'agencies/anomaly_list.html', {
        'anomalies': qs,
        'stats': stats,
        'current_status': status_filter,
        'current_severity': severity_filter,
        'current_category': category_filter,
        'search_query': q,
        'categories': AuditAnomaly.CATEGORY_CHOICES,
        'severities': AuditAnomaly.SEVERITY_CHOICES,
    })


@manager_required
def anomaly_detail(request, pk):
    """
    Vue détaillée d'une anomalie avec rapport d'analyse Gemini et panneau d'arbitrage humain.
    """
    agency = get_current_agency(request)
    anomaly = get_object_or_404(
        AuditAnomaly.objects.select_related('booking', 'departure', 'cashier', 'resolved_by', 'agency'),
        id=pk,
        agency=agency
    )
    return render(request, 'agencies/anomaly_detail.html', {
        'anomaly': anomaly,
    })


@manager_required
@require_POST
def anomaly_arbitrate(request, pk):
    """
    Arbitrage humain obligatoire : 'Gemini signale, un humain tranche'.
    Permet au responsable d'agence de valider, sanctionner, classer sans suite ou régulariser.
    """
    agency = get_current_agency(request)
    anomaly = get_object_or_404(AuditAnomaly, id=pk, agency=agency)

    decision = request.POST.get('decision')
    notes = request.POST.get('resolution_notes', '').strip()
    action = request.POST.get('apply_action', '').strip()

    if decision not in ['confirmed', 'dismissed', 'regularized']:
        messages.error(request, "Décision d'arbitrage invalide.")
        return redirect('agencies:anomaly_detail', pk=anomaly.pk)

    action_taken_desc = ""

    # Actions opérationnelles optionnelles liées à la décision
    if decision == 'confirmed':
        if action == 'cancel_booking' and anomaly.booking:
            if anomaly.booking.cancel():
                action_taken_desc = f"Billet {anomaly.booking.reference} officiellement annulé et {anomaly.booking.seats_reserved} place(s) remise(s) en vente."
        elif action == 'suspend_cashier' and anomaly.cashier:
            anomaly.cashier.is_active = False
            anomaly.cashier.save(update_fields=['is_active'])
            action_taken_desc = f"Compte de l'agent {anomaly.cashier.get_full_name() or anomaly.cashier.username} suspendu pour enquête interne."
    elif decision == 'regularized':
        if action == 'mark_paid' and anomaly.booking:
            anomaly.booking.payment_status = 'paid'
            if not anomaly.booking.paid_at:
                anomaly.booking.paid_at = timezone.now()
            anomaly.booking.save(update_fields=['payment_status', 'paid_at'])
            action_taken_desc = f"Paiement de {anomaly.booking.total_amount:,.0f} FCFA régularisé et encaissé au guichet."

    anomaly.status = decision
    anomaly.resolved_by = request.user
    anomaly.resolved_at = timezone.now()
    anomaly.resolution_notes = notes
    anomaly.action_taken = action_taken_desc
    anomaly.save()

    # Marquer les notifications associées comme lues
    anomaly.notifications.update(is_read=True)

    status_labels = {
        'confirmed': 'Fraude / Incohérence confirmée et sanctionnée',
        'dismissed': 'Classée sans suite (Fausse alerte)',
        'regularized': 'Régularisée avec succès'
    }
    messages.success(request, f"Arbitrage enregistré : {status_labels.get(decision, decision)}. {action_taken_desc}")
    return redirect('agencies:anomaly_list')


@manager_required
@require_POST
def anomaly_run_audit(request):
    """
    Déclencheur d'audit Gemini (asynchrone ou direct) depuis l'interface de l'agence.
    """
    from .services.gemini_audit import GeminiAuditService
    agency = get_current_agency(request)
    if not agency:
        return JsonResponse({'status': 'error', 'message': "Aucune agence active"}, status=400)

    # Exécution de l'audit
    audit_service = GeminiAuditService(agency=agency)
    result = audit_service.run_audit_sync()

    new_anomalies = result.get('total_new_anomalies', 0)
    msg = f"Audit Gemini terminé : {new_anomalies} nouvelle(s) anomalie(s) détectée(s) et signalée(s) au centre de traitement."
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({
            'status': 'success',
            'message': msg,
            'new_anomalies': new_anomalies
        })

    messages.success(request, msg)
    return redirect('agencies:anomaly_list')


@login_required(login_url='agencies:login')
def notification_list(request):
    """
    Historique des notifications et alertes de l'agence.
    """
    agency = get_current_agency(request)
    notifications = AgencyNotification.objects.filter(agency=agency).select_related('anomaly').order_by('-created_at')
    
    # Marquer comme lues
    unread = notifications.filter(is_read=False)
    if request.GET.get('mark_read') == '1':
        unread.update(is_read=True)
        messages.info(request, "Toutes les notifications ont été marquées comme lues.")
        return redirect('agencies:notification_list')

    return render(request, 'agencies/notification_list.html', {
        'notifications': notifications,
        'unread_count': unread.count(),
    })


@login_required(login_url='agencies:login')
@require_POST
def mark_notifications_read(request):
    agency = get_current_agency(request)
    AgencyNotification.objects.filter(agency=agency, is_read=False).update(is_read=True)
    messages.success(request, "Toutes les notifications ont été marquées comme lues.")
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('agencies:dashboard')
    return redirect(next_url)

@login_required(login_url='agencies:login')
@require_POST
def send_booking_reminder(request, booking_id):
    """
    Envoie un email de rappel manuel à un client.
    """
    agency = get_current_agency(request)
    if not agency:
        return JsonResponse({'status': 'error', 'message': "Agence introuvable."}, status=403)
        
    booking = get_object_or_404(Booking, id=booking_id, departure__agency=agency)
    
    if not booking.traveler_email:
        messages.error(request, "Impossible d'envoyer un rappel : Ce passager n'a pas d'adresse email.")
        return redirect(request.META.get('HTTP_REFERER', 'agencies:booking_list'))
        
    from accounts.services.email_service import send_trip_reminder_email
    success = send_trip_reminder_email(booking)
    
    if success:
        messages.success(request, f"Rappel envoyé avec succès à {booking.traveler_email}.")
    else:
        messages.error(request, "Une erreur s'est produite lors de l'envoi de l'email.")
        
    return redirect(request.META.get('HTTP_REFERER', 'agencies:booking_list'))




# --- Gestion des Lignes (Prix des trajets) ---
class LineListView(ManagerRequiredMixin, ListView):
    model = Line
    template_name = 'agencies/line_list.html'
    context_object_name = 'lines'

    def get_queryset(self):
        agency = get_current_agency(self.request)['current_agency']
        return Line.objects.filter(agency=agency).order_by('name')

class LineUpdateView(ManagerRequiredMixin, UpdateView):
    model = Line
    from .forms import LineForm
    form_class = LineForm
    template_name = 'agencies/line_form.html'
    success_url = reverse_lazy('agencies:line_list')

    def get_queryset(self):
        agency = get_current_agency(self.request)['current_agency']
        return Line.objects.filter(agency=agency)

    def form_valid(self, form):
        messages.success(self.request, 'Le prix du trajet a été mis à jour avec succès.')
        return super().form_valid(form)
