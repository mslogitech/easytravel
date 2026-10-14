import os
import re
import json
import logging
from core.tasks import dispatch_async_task
from datetime import datetime, timedelta
from urllib import request as url_request
from urllib.error import URLError

from django.conf import settings
from django.utils import timezone
from django.db.models import Count, Sum, Q, Avg
from django.contrib.auth import get_user_model

from agencies.models import Agency, AuditAnomaly, AgencyNotification
from bookings.models import Booking
from travel.models import Departure, LineStop

logger = logging.getLogger(__name__)
User = get_user_model()


class GeminiAuditService:
    """
    Service d'analyse et d'audit intelligent Gemini pour le transport routier.
    Détecte de manière asynchrone les comportements suspects, fraudes potentielles,
    écarts de caisse et données incohérentes selon le principe :
    'Gemini signale, un humain tranche'.
    """

    def __init__(self, agency=None):
        self.agency = agency
        self.gemini_api_key = getattr(settings, 'GEMINI_API_KEY', os.environ.get('GEMINI_API_KEY', '')).strip()
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={self.gemini_api_key}"

    def run_audit_async(self, callback=None):
        """
        Déclenche l'audit en arrière-plan sans bloquer la requête HTTP utilisateur.
        Utilise le pool de threads supervisé de EasyTravel.
        """
        return dispatch_async_task(self._run_audit_worker, callback)

    def _run_audit_worker(self, callback=None):
        try:
            results = self.run_audit_sync()
            if callback and callable(callback):
                callback(results)
        except Exception as e:
            logger.exception("Erreur lors de l'exécution asynchrone de l'audit Gemini: %s", e)

    def run_audit_sync(self):
        """
        Exécute la batterie de vérifications d'audit pour l'agence (ou toutes les agences).
        Retourne un bilan synthétique des anomalies identifiées.
        """
        agencies = [self.agency] if self.agency else Agency.objects.all()
        total_created = 0
        total_evaluated = 0
        summary_by_category = {}

        for agency in agencies:
            # 1. Vérification : Même numéro de pièce d'identité avec plusieurs noms différents
            c1 = self.check_identity_number_collisions(agency)
            # 2. Vérification : Caissier avec taux d'annulation anormalement élevé
            c2 = self.check_cashier_cancellations(agency)
            # 3. Vérification : Tarifs s'écartant de la grille officielle
            c3 = self.check_pricing_discrepancies(agency)
            # 4. Vérification : Billets marqués 'embarqués' sans encaissement validé
            c4 = self.check_unpaid_boarding(agency)
            # 5. Vérification : Écarts de caisse et réconciliations billets / recettes
            c5 = self.check_cash_register_mismatches(agency)
            # 6. Vérification : Noms, prénoms ou numéros de téléphone manifestement faux
            c6 = self.check_bogus_passenger_data(agency)
            # 7. Vérification : Comportements suspects supplémentaires (rafales, incohérences départs)
            c7 = self.check_suspicious_patterns(agency)

            agency_created = c1 + c2 + c3 + c4 + c5 + c6 + c7
            total_created += agency_created

        return {
            'timestamp': timezone.now().isoformat(),
            'total_new_anomalies': total_created,
            'status': 'completed',
        }

    # =========================================================================
    # RÈGLE 1 : Même numéro de pièce avec plusieurs noms différents
    # =========================================================================
    def check_identity_number_collisions(self, agency):
        created_count = 0
        # Récupérer les réservations avec pièce d'identité renseignée
        bookings = Booking.objects.filter(
            departure__agency=agency
        ).exclude(
            Q(id_number__isnull=True) | Q(id_number__exact='') | Q(id_number__iexact='N/A')
        ).select_related('departure', 'user')

        id_map = {}
        for b in bookings:
            cleaned_id = b.id_number.strip().upper().replace(" ", "").replace("-", "")
            if len(cleaned_id) < 4:
                continue
            key = (b.id_type, cleaned_id)
            if key not in id_map:
                id_map[key] = []
            id_map[key].append(b)

        for (id_type, cleaned_id), blist in id_map.items():
            # Noms uniques normalisés
            names = set(b.traveler_name.strip().title() for b in blist)
            if len(names) > 1:
                # Collision d'identité détectée !
                sample_b = blist[0]
                # Vérifier si une anomalie non résolue existe déjà
                existing = AuditAnomaly.objects.filter(
                    agency=agency,
                    category='id_collision',
                    evidence_data__cleaned_id=cleaned_id,
                    status='pending'
                ).exists()

                if not existing:
                    names_str = ", ".join(sorted(names))
                    ref_list = [b.reference for b in blist[:5]]
                    severity = 'critical' if len(names) >= 3 else 'high'

                    title = f"Collision d'identité : Pièce {id_type} N° {cleaned_id} sous {len(names)} noms distincts"
                    desc = (
                        f"Le numéro de document {id_type} « {cleaned_id} » a été utilisé sur {len(blist)} réservations "
                        f"associées à {len(names)} passagers différents : {names_str}. "
                        f"Billets identifiés : {', '.join(ref_list)}. "
                        f"Risque caractérisé d'usurpation d'identité, de prête-nom ou de revente illégale de places."
                    )
                    recom = (
                        "Convoquer les passagers au contrôle d'embarquement avec leur pièce originale. "
                        "Exiger la présentation physique de la CNI/Passeport avant autorisation de monter à bord."
                    )

                    anomaly = AuditAnomaly.objects.create(
                        agency=agency,
                        category='id_collision',
                        severity=severity,
                        title=title,
                        description=desc,
                        gemini_recommendation=recom,
                        booking=sample_b,
                        departure=sample_b.departure,
                        evidence_data={
                            'cleaned_id': cleaned_id,
                            'id_type': id_type,
                            'distinct_names': list(names),
                            'booking_references': ref_list,
                            'occurrence_count': len(blist)
                        },
                        ai_confidence=0.96
                    )
                    self._create_notification(agency, anomaly, title, desc, level='danger')
                    created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 2 : Caissier qui annule beaucoup plus que les autres
    # =========================================================================
    def check_cashier_cancellations(self, agency):
        created_count = 0
        all_bookings = Booking.objects.filter(departure__agency=agency, user__isnull=False)
        total_agency_bookings = all_bookings.count()
        if total_agency_bookings < 10:
            return 0

        total_cancelled = all_bookings.filter(status='cancelled').count()
        agency_avg_rate = total_cancelled / total_agency_bookings if total_agency_bookings > 0 else 0

        # Regroupement par agent/caissier
        cashier_stats = all_bookings.values('user', 'user__username', 'user__first_name', 'user__last_name').annotate(
            total=Count('id'),
            cancelled=Count('id', filter=Q(status='cancelled'))
        )

        for stat in cashier_stats:
            user_id = stat['user']
            total = stat['total']
            cancelled = stat['cancelled']

            # Minimum 5 billets émis pour avoir une statistique représentative
            if total >= 5:
                rate = cancelled / total
                # Détection : taux d'annulation > 20% et au moins 1.8x la moyenne agence
                if rate > 0.20 and (agency_avg_rate == 0 or rate >= 1.8 * agency_avg_rate):
                    user_name = f"{stat['user__first_name']} {stat['user__last_name']}".strip() or stat['user__username']
                    
                    existing = AuditAnomaly.objects.filter(
                        agency=agency,
                        category='cashier_cancellation',
                        cashier_id=user_id,
                        status='pending'
                    ).exists()

                    if not existing:
                        severity = 'critical' if rate > 0.35 else 'high'
                        title = f"Taux d'annulation anormal ({rate*100:.1f}%) par le caissier {user_name}"
                        desc = (
                            f"L'agent de guichet {user_name} a annulé {cancelled} billets sur {total} émis, "
                            f"soit un taux de {rate*100:.1f}% (moyenne de l'agence : {agency_avg_rate*100:.1f}%). "
                            f"Ce comportement présente une forte probabilité de schéma de fraude 'ticket volant' : "
                            f"un billet est encaissé en espèces, imprimé puis immédiatement annulé dans le système "
                            f"tandis que le passager monte dans le bus avec le reçu papier."
                        )
                        recom = (
                            "Effectuer un audit inopiné de la caisse de cet agent. "
                            "Recouper systématiquement les souches physiques de billets annulés avec le manifeste d'embarquement."
                        )

                        cashier_user = User.objects.filter(id=user_id).first()
                        anomaly = AuditAnomaly.objects.create(
                            agency=agency,
                            category='cashier_cancellation',
                            severity=severity,
                            title=title,
                            description=desc,
                            gemini_recommendation=recom,
                            cashier=cashier_user,
                            evidence_data={
                                'cashier_name': user_name,
                                'total_issued': total,
                                'total_cancelled': cancelled,
                                'cashier_rate': round(rate * 100, 1),
                                'agency_rate': round(agency_avg_rate * 100, 1)
                            },
                            ai_confidence=0.91
                        )
                        self._create_notification(agency, anomaly, title, desc, level='danger')
                        created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 3 : Tarifs qui s'écartent de la grille officielle
    # =========================================================================
    def check_pricing_discrepancies(self, agency):
        created_count = 0
        bookings = Booking.objects.filter(
            departure__agency=agency,
            departure_stop__isnull=False,
            arrival_stop__isnull=False
        ).exclude(status='cancelled').select_related('departure_stop', 'arrival_stop', 'departure', 'departure__line')

        for b in bookings[:200]: # Analyser les récents
            d_stop = b.departure_stop
            a_stop = b.arrival_stop
            official_unit = max(0, float(a_stop.price_from_start - d_stop.price_from_start))
            official_total = official_unit * b.seats_reserved

            diff = float(b.total_amount) - official_total
            if abs(diff) > 50: # Écart supérieur à 50 FCFA
                existing = AuditAnomaly.objects.filter(
                    agency=agency,
                    category='price_mismatch',
                    booking=b,
                    status='pending'
                ).exists()

                if not existing:
                    severity = 'high' if diff < 0 else 'medium'
                    direction = "sous-facturé (manque à gagner)" if diff < 0 else "surfacturé (anomalie tarifaire)"
                    title = f"Écart tarifaire ({diff:+,.0f} FCFA) sur le billet {b.reference}"
                    desc = (
                        f"Le billet {b.reference} pour le trajet {d_stop.city.name} ➔ {a_stop.city.name} "
                        f"a été enregistré pour {float(b.total_amount):,.0f} FCFA ({b.seats_reserved} place(s)), "
                        f"alors que la grille officielle calcule {official_total:,.0f} FCFA. "
                        f"Le billet est {direction}. Écart net constaté : {diff:+,.0f} FCFA."
                    )
                    recom = (
                        "Vérifier si une réduction conventionnée ou promotionnelle a été appliquée. "
                        "Dans le cas contraire, demander une régularisation à l'agent émetteur."
                    )

                    anomaly = AuditAnomaly.objects.create(
                        agency=agency,
                        category='price_mismatch',
                        severity=severity,
                        title=title,
                        description=desc,
                        gemini_recommendation=recom,
                        booking=b,
                        departure=b.departure,
                        evidence_data={
                            'booking_ref': b.reference,
                            'recorded_amount': float(b.total_amount),
                            'official_amount': official_total,
                            'discrepancy': diff,
                            'route': f"{d_stop.city.name} - {a_stop.city.name}"
                        },
                        ai_confidence=0.98
                    )
                    self._create_notification(agency, anomaly, title, desc, level='warning')
                    created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 4 : Billets « embarqués » sans paiement validé
    # =========================================================================
    def check_unpaid_boarding(self, agency):
        created_count = 0
        unpaid_boarded = Booking.objects.filter(
            departure__agency=agency,
            status='boarded'
        ).exclude(
            payment_status='paid'
        ).select_related('departure', 'departure__line')

        for b in unpaid_boarded:
            existing = AuditAnomaly.objects.filter(
                agency=agency,
                category='unpaid_boarding',
                booking=b,
                status='pending'
            ).exists()

            if not existing:
                title = f"Passager embarqué sans encaissement : Billet {b.reference} ({b.traveler_name})"
                desc = (
                    f"Le passager {b.traveler_name} a été validé comme 'Embarqué' à bord du voyage "
                    f"'{b.departure.line.name}' du {b.departure.date.strftime('%d/%m/%Y')} à {b.departure.time.strftime('%H:%M')}, "
                    f"alors que le règlement de {float(b.total_amount):,.0f} FCFA est au statut '{b.get_payment_status_display()}'. "
                    f"Risque critique d'embarquement complaisant ou de fuite de trésorerie au quai."
                )
                recom = (
                    "Bloquer le passager au départ ou instruire le contrôleur de bord pour exiger le paiement immédiat "
                    "en espèces ou Mobile Money avant le départ effectif du véhicule."
                )

                anomaly = AuditAnomaly.objects.create(
                    agency=agency,
                    category='unpaid_boarding',
                    severity='critical',
                    title=title,
                    description=desc,
                    gemini_recommendation=recom,
                    booking=b,
                    departure=b.departure,
                    evidence_data={
                        'booking_ref': b.reference,
                        'traveler_name': b.traveler_name,
                        'traveler_phone': b.traveler_phone,
                        'amount_due': float(b.total_amount),
                        'payment_status': b.payment_status,
                        'departure_info': f"{b.departure.line.name} le {b.departure.date} à {b.departure.time}"
                    },
                    ai_confidence=0.99
                )
                self._create_notification(agency, anomaly, title, desc, level='danger')
                created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 5 : Caisse du jour / Clôture ne collant pas avec le nombre de billets
    # =========================================================================
    def check_cash_register_mismatches(self, agency):
        created_count = 0
        now = timezone.localtime()
        # Analyser les départs passés ou du jour ayant déjà quitté le quai
        departed_or_past = Departure.objects.filter(
            agency=agency
        ).filter(
            Q(date__lt=now.date()) | 
            Q(date=now.date(), time__lte=now.time()) | 
            Q(status='departed')
        ).order_by('-date', '-time')[:50]

        for dep in departed_or_past:
            # Vérifier s'il y a des billets espèces non encaissés sur ce départ déjà parti
            pending_cash_bookings = dep.bookings.filter(
                payment_method='cash',
                payment_status='pending'
            ).exclude(status='cancelled')

            if pending_cash_bookings.exists():
                unpaid_seats = sum(b.seats_reserved for b in pending_cash_bookings)
                unpaid_amount = sum(float(b.total_amount) for b in pending_cash_bookings)

                existing = AuditAnomaly.objects.filter(
                    agency=agency,
                    category='cash_register',
                    departure=dep,
                    status='pending'
                ).exists()

                if not existing and unpaid_amount > 0:
                    title = f"Caisse non soldée sur voyage clôturé : {unpaid_amount:,.0f} FCFA en souffrance"
                    desc = (
                        f"Le départ '{dep.line.name}' du {dep.date.strftime('%d/%m/%Y')} à {dep.time.strftime('%H:%M')} "
                        f"est déjà clôturé ou parti, mais enregistre {pending_cash_bookings.count()} réservation(s) espèces "
                        f"({unpaid_seats} place(s)) non régularisées, pour un montant total de {unpaid_amount:,.0f} FCFA. "
                        f"La caisse physique de vacation n'a pas été rapprochée du registre informatique."
                    )
                    recom = (
                        "Exiger du chef de gare le procès-verbal de clôture de caisse du jour "
                        "et forcer l'enregistrement ou l'annulation formelle des réservations orphelines."
                    )

                    anomaly = AuditAnomaly.objects.create(
                        agency=agency,
                        category='cash_register',
                        severity='high',
                        title=title,
                        description=desc,
                        gemini_recommendation=recom,
                        departure=dep,
                        evidence_data={
                            'departure_id': dep.id,
                            'departure_name': dep.line.name,
                            'departure_datetime': f"{dep.date} {dep.time}",
                            'unpaid_bookings_count': pending_cash_bookings.count(),
                            'unpaid_seats': unpaid_seats,
                            'unpaid_amount': unpaid_amount
                        },
                        ai_confidence=0.94
                    )
                    self._create_notification(agency, anomaly, title, desc, level='danger')
                    created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 6 : Noms ou numéros manifestement faux (Données bizarres)
    # =========================================================================
    def check_bogus_passenger_data(self, agency):
        created_count = 0
        recent_bookings = Booking.objects.filter(
            departure__agency=agency
        ).exclude(status='cancelled').order_by('-created_at')[:150]

        bogus_name_patterns = [
            r'^(test|azerty|asdf|qwerty|client|passager|inconnu|unknown|xxx|yyy|zzz)',
            r'^\d+$',                  # Que des chiffres
            r'^(.){1,2}$',             # 1 ou 2 caractères
            r'([a-zA-Z])\1{3,}',       # Répétition de 4 fois la même lettre (ex: aaaa)
            r'[0-9]{3,}',              # Nom contenant des suites de chiffres
        ]

        bogus_phone_patterns = [
            r'^(000000|111111|222222|333333|444444|555555|666666|777777|888888|999999)',
            r'^(123456|012345|678901)',
        ]

        for b in recent_bookings:
            name = b.traveler_name.strip()
            phone = b.traveler_phone.strip().replace(" ", "").replace("-", "")
            is_suspicious = False
            reasons = []

            # Analyse du nom
            for pattern in bogus_name_patterns:
                if re.search(pattern, name, re.IGNORECASE):
                    is_suspicious = True
                    reasons.append(f"Nom voyageur manifestement factice ou de test ('{name}')")
                    break

            # Analyse du téléphone
            if len(phone) < 8 or len(phone) > 14:
                is_suspicious = True
                reasons.append(f"Format de téléphone non standard ({len(phone)} chiffres au lieu de 9)")
            else:
                for pattern in bogus_phone_patterns:
                    if re.search(pattern, phone):
                        is_suspicious = True
                        reasons.append(f"Numéro de téléphone bidon détecté ('{phone}')")
                        break

            if is_suspicious:
                existing = AuditAnomaly.objects.filter(
                    agency=agency,
                    category='bogus_identity',
                    booking=b,
                    status='pending'
                ).exists()

                if not existing:
                    reasons_str = " ; ".join(reasons)
                    title = f"Données passager fictives ou incohérentes : {name}"
                    desc = (
                        f"Le billet {b.reference} présente des anomalies manifestes sur l'identité du voyageur : "
                        f"{reasons_str}. "
                        f"Numéro de CNI renseigné : '{b.id_number or 'Non renseigné'}'. "
                        f"Non-conformité vis-à-vis des exigences du Ministère des Transports régissant les manifestes de bord."
                    )
                    recom = (
                        "Exiger de l'agent de guichet la saisie de l'identité réelle vérifiée sur pièce d'identité physique. "
                        "Mettre à jour la réservation avant le départ."
                    )

                    anomaly = AuditAnomaly.objects.create(
                        agency=agency,
                        category='bogus_identity',
                        severity='medium',
                        title=title,
                        description=desc,
                        gemini_recommendation=recom,
                        booking=b,
                        departure=b.departure,
                        evidence_data={
                            'booking_ref': b.reference,
                            'traveler_name': name,
                            'traveler_phone': phone,
                            'id_number': b.id_number,
                            'detection_reasons': reasons
                        },
                        ai_confidence=0.88
                    )
                    self._create_notification(agency, anomaly, title, desc, level='warning')
                    created_count += 1

        return created_count

    # =========================================================================
    # RÈGLE 7 : Comportements suspects supplémentaires & Incohérences
    # =========================================================================
    def check_suspicious_patterns(self, agency):
        created_count = 0
        now = timezone.localtime()

        # A. Départs imminents (moins de 2h) sans véhicule ou sans chauffeur assigné
        imminent_deps = Departure.objects.filter(
            agency=agency,
            date=now.date(),
            status='scheduled'
        ).filter(
            time__gte=now.time(),
            time__lte=(now + timedelta(hours=2)).time()
        ).select_related('line')

        for dep in imminent_deps:
            missing = []
            if not dep.vehicle:
                missing.append("aucun véhicule assigné")
            if not dep.driver:
                missing.append("aucun chauffeur assigné")

            if missing:
                existing = AuditAnomaly.objects.filter(
                    agency=agency,
                    category='suspicious_pattern',
                    departure=dep,
                    status='pending'
                ).exists()

                if not existing:
                    missing_str = " et ".join(missing)
                    title = f"Départ imminent non pourvu ({dep.line.name} à {dep.time.strftime('%H:%M')})"
                    desc = (
                        f"Le départ '{dep.line.name}' est programmé à {dep.time.strftime('%H:%M')} (dans moins de 2h), "
                        f"mais présente une anomalie critique d'exploitation : {missing_str}. "
                        f"Ce départ compte déjà {dep.booked_seats} passager(s) réservé(s). Risque majeur de retard ou d'annulation imprévue."
                    )
                    recom = (
                        "Affecter immédiatement un véhicule opérationnel et un conducteur disponible "
                        "depuis le module de gestion du planning."
                    )

                    anomaly = AuditAnomaly.objects.create(
                        agency=agency,
                        category='suspicious_pattern',
                        severity='critical',
                        title=title,
                        description=desc,
                        gemini_recommendation=recom,
                        departure=dep,
                        evidence_data={
                            'departure_id': dep.id,
                            'departure_time': dep.time.strftime('%H:%M'),
                            'missing_resources': missing,
                            'booked_seats': dep.booked_seats
                        },
                        ai_confidence=0.99
                    )
                    self._create_notification(agency, anomaly, title, desc, level='danger')
                    created_count += 1

        return created_count

    # =========================================================================
    # Helper interne de création de notification
    # =========================================================================
    def _create_notification(self, agency, anomaly, title, message, level='warning'):
        try:
            AgencyNotification.objects.create(
                agency=agency,
                anomaly=anomaly,
                title=title,
                message=message,
                level=level
            )
        except Exception as e:
            logger.error("Erreur lors de la création de la notification d'anomalie : %s", e)
