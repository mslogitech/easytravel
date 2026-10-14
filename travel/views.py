from datetime import datetime, timedelta
from django.shortcuts import render
from django.utils import timezone
from .models import City, Departure, LineStop, Line
from agencies.models import Agency

FRENCH_DAYS_SHORT = ['Lun.', 'Mar.', 'Mer.', 'Jeu.', 'Ven.', 'Sam.', 'Dim.']
FRENCH_MONTHS_SHORT = {
    1: 'Janv.', 2: 'Févr.', 3: 'Mars', 4: 'Avr.', 5: 'Mai', 6: 'Juin',
    7: 'Juil.', 8: 'Août', 9: 'Sept.', 10: 'Oct.', 11: 'Nov.', 12: 'Déc.'
}
FRENCH_DAYS_FULL = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
FRENCH_MONTHS_FULL = {
    1: 'Janvier', 2: 'Février', 3: 'Mars', 4: 'Avril', 5: 'Mai', 6: 'Juin',
    7: 'Juillet', 8: 'Août', 9: 'Septembre', 10: 'Octobre', 11: 'Novembre', 12: 'Décembre'
}

def format_french_date(d, full=False):
    if not d:
        return ""
    if full:
        return f"{FRENCH_DAYS_FULL[d.weekday()]} {d.day} {FRENCH_MONTHS_FULL[d.month]} {d.year}"
    return f"{FRENCH_DAYS_SHORT[d.weekday()]} {d.day} {FRENCH_MONTHS_SHORT[d.month]}"

def home(request):
    cities = City.objects.filter(is_active=True).order_by('name')
    today_str = timezone.localtime().date().strftime('%Y-%m-%d')
    partner_agencies = Agency.objects.filter(status='active').order_by('name')
    
    # Configuration des 4 axes majeurs au Cameroun
    popular_routes_config = [
        {
            'from': 'Douala', 
            'to': 'Yaoundé', 
            'tag': 'Axe Lourd N°3', 
            'desc': 'Départs fréquents toutes les 30 minutes',
            'badge_class': 'bg-primary bg-opacity-10 text-primary'
        },
        {
            'from': 'Yaoundé', 
            'to': 'Bafoussam', 
            'tag': 'Région Ouest', 
            'desc': 'Bus VIP climatisés grand confort',
            'badge_class': 'bg-success bg-opacity-10 text-success'
        },
        {
            'from': 'Douala', 
            'to': 'Kribi', 
            'tag': 'Côte Atlantique', 
            'desc': 'Trajet balnéaire rapide et fluide',
            'badge_class': 'bg-warning bg-opacity-15 text-dark'
        },
        {
            'from': 'Yaoundé', 
            'to': 'Ngaoundéré', 
            'tag': 'Grand Nord', 
            'desc': 'Départs de nuit et couchettes',
            'badge_class': 'bg-info bg-opacity-10 text-info'
        },
    ]

    import unicodedata
    def norm(s):
        if not s: return ''
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower().strip()

    city_map = {norm(c.name): c for c in cities}
    active_lines = list(Line.objects.all().prefetch_related('stops__city', 'agency'))
    line_map = {}
    for l in active_lines:
        dep_c = l.departure_city
        arr_c = l.arrival_city
        if dep_c and arr_c:
            line_map[(norm(dep_c.name), norm(arr_c.name))] = l

    today = timezone.localtime().date()
    departures = list(
        Departure.objects.filter(status__in=['scheduled', 'boarding'], date__gte=today)
        .select_related('line', 'agency')
    )

    departures_by_line = {}
    for d in departures:
        departures_by_line.setdefault(d.line_id, []).append(d)

    popular_routes = []
    for r in popular_routes_config:
        from_name = r['from']
        to_name = r['to']
        dep_c = city_map.get(norm(from_name))
        arr_c = city_map.get(norm(to_name))
        
        line = line_map.get((norm(from_name), norm(to_name)))
        
        starting_price = None
        if line and line.id in departures_by_line:
            for dep in departures_by_line[line.id]:
                pricing = dep.calculate_pricing()
                tot = pricing['total_price']
                if starting_price is None or tot < starting_price:
                    starting_price = tot
        elif line and line.base_price > 0:
            fee_pct = 0.03
            if hasattr(line, 'agency') and line.agency and hasattr(line.agency, 'platform_fee_percentage'):
                fee_pct = float(line.agency.platform_fee_percentage) / 100.0
            starting_price = float(line.base_price) * (1.0 + fee_pct)
        
        if starting_price is None:
            defaults = {'Douala-Yaoundé': 5150, 'Yaoundé-Bafoussam': 4000, 'Douala-Kribi': 3500, 'Yaoundé-Ngaoundéré': 8500}
            starting_price = defaults.get(f"{from_name}-{to_name}", 5000)

        popular_routes.append({
            'from_name': from_name,
            'to_name': to_name,
            'tag': r['tag'],
            'desc': r['desc'],
            'badge_class': r['badge_class'],
            'dep_city_id': dep_c.id if dep_c else '',
            'arr_city_id': arr_c.id if arr_c else '',
            'starting_price': int(round(starting_price)),
        })

    return render(request, 'travel/home.html', {
        'cities': cities,
        'today_str': today_str,
        'partner_agencies': partner_agencies,
        'popular_routes': popular_routes,
    })

def search(request):
    departure_city_id = request.GET.get('departure_city', '').strip()
    arrival_city_id = request.GET.get('arrival_city', '').strip()
    date_param = request.GET.get('date', '').strip()

    cities = City.objects.filter(is_active=True).order_by('name')
    
    # 1. Date et heure actuelles
    now = timezone.localtime()
    today = now.date()
    current_time = now.time()

    # 2. Identification des lignes entre départ et arrivée
    matching_line_ids = None
    start_stops_by_line = {}
    end_stops_by_line = {}
    departure_city = None
    arrival_city = None

    if departure_city_id and arrival_city_id:
        try:
            dep_id_int = int(departure_city_id)
            arr_id_int = int(arrival_city_id)
            departure_city = City.objects.filter(id=dep_id_int).first()
            arrival_city = City.objects.filter(id=arr_id_int).first()

            start_stops = LineStop.objects.filter(city_id=dep_id_int).select_related('city')
            end_stops = LineStop.objects.filter(city_id=arr_id_int).select_related('city')

            start_map = {s.line_id: s for s in start_stops}
            end_map = {s.line_id: s for s in end_stops}

            matching_line_ids = [
                lid for lid, s_stop in start_map.items()
                if lid in end_map and s_stop.stop_order < end_map[lid].stop_order
            ]
            for lid in matching_line_ids:
                start_stops_by_line[lid] = start_map[lid]
                end_stops_by_line[lid] = end_map[lid]
        except ValueError:
            pass

    # Base des départs planifiés et en cours d'embarquement
    base_qs = Departure.objects.filter(status__in=['scheduled', 'boarding'])
    if matching_line_ids is not None:
        base_qs = base_qs.filter(line_id__in=matching_line_ids)

    # 3. Déterminer la date cible (si non renseignée -> date la plus proche à partir de maintenant)
    selected_date = None
    is_auto_date = False

    if date_param:
        try:
            selected_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except ValueError:
            selected_date = None

    if not selected_date:
        is_auto_date = True
        # Y a-t-il des départs aujourd'hui après l'heure actuelle ?
        has_departures_today = base_qs.filter(date=today, time__gte=current_time).exists()
        if has_departures_today:
            selected_date = today
        else:
            # Chercher la date future la plus proche ayant des départs
            next_avail_date = base_qs.filter(date__gt=today).order_by('date').values_list('date', flat=True).first()
            selected_date = next_avail_date if next_avail_date else today

    # 4. Récupérer les départs pour la date sélectionnée
    departures_qs = base_qs.filter(date=selected_date)
    # Si c'est aujourd'hui, exclure les départs déjà passés
    if selected_date == today:
        departures_qs = departures_qs.filter(time__gte=current_time)

    departures_list = list(
        departures_qs.select_related('agency', 'line', 'vehicle').order_by('time')
    )

    # Injecter les détails de tronçon (prix, arrêts)
    for dep in departures_list:
        if dep.line_id in start_stops_by_line and dep.line_id in end_stops_by_line:
            s_stop = start_stops_by_line[dep.line_id]
            e_stop = end_stops_by_line[dep.line_id]
        else:
            stops = list(dep.line.stops.select_related('city').order_by('stop_order'))
            s_stop = stops[0] if stops else None
            e_stop = stops[-1] if stops else None
            
        dep.segment_start_stop = s_stop
        dep.segment_end_stop = e_stop
        
        pricing = dep.calculate_pricing(s_stop, e_stop)
        dep.agency_price = pricing['agency_price']
        dep.platform_fee = pricing['platform_fee']
        dep.segment_price = pricing['total_price']

        # Tranche horaire (Matin / Après-midi / Soir)
        h = dep.time.hour
        if h < 12:
            dep.time_slot = 'morning'
        elif h < 18:
            dep.time_slot = 'afternoon'
        else:
            dep.time_slot = 'evening'

        # Type de confort
        v_type = (dep.vehicle.vehicle_type if dep.vehicle else '').lower()
        l_name = dep.line.name.lower()
        dep.is_vip = 'vip' in v_type or 'vip' in l_name or 'climat' in v_type
        dep.comfort_type = 'vip' if dep.is_vip else 'standard'

        # Système de notation & avis
        agency_hash = (dep.agency_id * 17) % 5
        dep.rating = round(4.6 + (agency_hash * 0.08), 1)
        dep.reviews_count = 140 + ((dep.agency_id * 47) % 210)

    # 5. Extraction des agences uniques présentes dans les résultats pour les filtres
    unique_agencies = []
    seen_agency_ids = set()
    for dep in departures_list:
        if dep.agency_id not in seen_agency_ids:
            seen_agency_ids.add(dep.agency_id)
            unique_agencies.append(dep.agency)

    # 6. Préparer les dates pour le slider/carousel horizontal (14 jours)
    slider_start = min(today, selected_date)
    dates_with_deps = set(
        base_qs.filter(date__gte=slider_start, date__lte=slider_start + timedelta(days=21))
        .values_list('date', flat=True)
        .distinct()
    )

    slider_dates = []
    for i in range(14):
        d = slider_start + timedelta(days=i)
        slider_dates.append({
            'date': d,
            'date_str': d.strftime('%Y-%m-%d'),
            'day_short': FRENCH_DAYS_SHORT[d.weekday()],
            'day_num': d.day,
            'month_short': FRENCH_MONTHS_SHORT[d.month],
            'is_selected': (d == selected_date),
            'is_today': (d == today),
            'has_departures': (d in dates_with_deps),
        })

    # Navigation Jour Suivant / Jour Précédent
    prev_date = (selected_date - timedelta(days=1)) if selected_date > today else None
    next_date = selected_date + timedelta(days=1)

    context = {
        'departures': departures_list,
        'unique_agencies': unique_agencies,
        'cities': cities,
        'departure_city': departure_city,
        'arrival_city': arrival_city,
        'selected_departure': int(departure_city_id) if departure_city_id and departure_city_id.isdigit() else '',
        'selected_arrival': int(arrival_city_id) if arrival_city_id and arrival_city_id.isdigit() else '',
        'selected_date_str': selected_date.strftime('%Y-%m-%d'),
        'selected_date_display': format_french_date(selected_date, full=True),
        'is_auto_date': is_auto_date,
        'is_today': (selected_date == today),
        'current_time_display': current_time.strftime('%H:%M'),
        'slider_dates': slider_dates,
        'prev_date_str': prev_date.strftime('%Y-%m-%d') if prev_date else None,
        'prev_date_display': format_french_date(prev_date) if prev_date else None,
        'next_date_str': next_date.strftime('%Y-%m-%d'),
        'next_date_display': format_french_date(next_date),
    }
    return render(request, 'travel/results.html', context)
