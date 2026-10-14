from urllib.parse import quote
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.core.exceptions import ValidationError
from travel.models import Departure, LineStop
from accounts.models import User
from accounts.validators import (
    validate_cameroon_phone, 
    validate_clean_email, 
    validate_passenger_name
)
from accounts.services.email_service import send_account_activation_email
from .models import Booking
import qrcode
import base64
import hmac
import hashlib
from io import BytesIO
from django.contrib import messages
from django.conf import settings
from django.db import transaction


def book_departure(request, departure_id):
    """
    Vue de réservation d'un voyage :
    - Trajet défini automatiquement du premier au dernier arrêt de la ligne.
    - Réservation sans numéro de CNI obligatoire.
    """
    departure = get_object_or_404(
        Departure.objects.select_related('agency', 'line', 'vehicle'), 
        id=departure_id
    )
    
    # Détermination précise du tronçon demandé (via paramètres GET ou formulaire POST)
    start_id = request.POST.get('start_id') or request.POST.get('start_stop_id') or request.GET.get('start')
    end_id = request.POST.get('end_id') or request.POST.get('end_stop_id') or request.GET.get('end')
    
    start_stop = None
    end_stop = None
    if start_id and end_id:
        try:
            start_stop = departure.line.stops.select_related('city').get(id=int(start_id))
            end_stop = departure.line.stops.select_related('city').get(id=int(end_id))
        except (LineStop.DoesNotExist, ValueError):
            start_stop = None
            end_stop = None
            
    if not start_stop or not end_stop or start_stop == end_stop:
        start_stop = departure.line.stops.select_related('city').order_by('stop_order').first()
        end_stop = departure.line.stops.select_related('city').order_by('-stop_order').first()
        
    if not start_stop or not end_stop or start_stop == end_stop:
        messages.error(request, "Erreur de configuration de la ligne de voyage.")
        return redirect('travel:home')
        
    # Tarification unique et centralisée (identique à la recherche et au paiement)
    pricing = departure.calculate_pricing(start_stop, end_stop)
    agency_unit_price = pricing['agency_price']
    platform_unit_fee = pricing['platform_fee']
    segment_price = pricing['total_price']
    
    if segment_price <= 0:
        messages.error(request, "Tarif calculé invalide.")
        return redirect('travel:home')
    
    error = None
    is_auth = request.user.is_authenticated
    current_user = request.user if is_auth else None

    raw_user_email = current_user.email if current_user else ''
    if raw_user_email and 'dup_' in raw_user_email:
        import re
        raw_user_email = re.sub(r'dup_\d+_', '', raw_user_email)

    form_data = {
        'name': f"{current_user.first_name} {current_user.last_name}".strip() if current_user else '',
        'last_name': current_user.last_name if current_user else '',
        'first_name': current_user.first_name if current_user else '',
        'email': raw_user_email,
        'phone': current_user.phone if current_user else '',
        'id_type': getattr(current_user, 'id_type', 'none') if current_user else 'none',
        'seats': 1,
    }
    
    if request.method == 'POST':
        last_name_raw = request.POST.get('last_name', '').strip()
        first_name_raw = request.POST.get('first_name', '').strip()
        name_raw = request.POST.get('name', '').strip()

        # Jumeler Nom et Prénom de manière fluide
        if last_name_raw or first_name_raw:
            name = f"{last_name_raw} {first_name_raw}".strip()
        else:
            name = name_raw
            if name_raw:
                parts = name_raw.split(' ', 1)
                first_name_raw = parts[0]
                last_name_raw = parts[1] if len(parts) > 1 else ''

        phone_raw = request.POST.get('phone', '').strip()
        email_raw = request.POST.get('email', '').strip()
        id_type_raw = request.POST.get('id_type', 'none').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        
        try:
            seats = int(request.POST.get('seats', 1))
        except (ValueError, TypeError):
            seats = 1
            
        form_data = {
            'name': name,
            'last_name': last_name_raw,
            'first_name': first_name_raw,
            'phone': phone_raw,
            'email': email_raw,
            'id_type': id_type_raw,
            'seats': seats,
        }
        
        try:
            clean_name = validate_passenger_name(name)
            clean_phone = validate_cameroon_phone(phone_raw)
            
            # Email is optional. If provided, validate it. Otherwise, create a placeholder for account creation.
            if email_raw:
                clean_email = validate_clean_email(email_raw)
            else:
                clean_email = None
                
            clean_id_number = "" # On ne stocke plus le numéro
            
            with transaction.atomic():
                departure.refresh_from_db()
                if departure.available_capacity < seats:
                    raise ValidationError(f"Désolé, il ne reste que {departure.available_capacity} places.")
                
                # Gestion Utilisateur
                if not is_auth:
                    if User.objects.filter(phone=clean_phone).exists():
                        raise ValidationError("Ce numéro de téléphone est déjà associé à un compte. Veuillez vous connecter.")
                    
                    # Generate a random 6-character password for the user
                    import random
                    import string
                    generated_password = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
                    
                    resolved_first = first_name_raw or (clean_name.split()[0] if clean_name else "")
                    resolved_last = last_name_raw or (' '.join(clean_name.split()[1:]) if len(clean_name.split()) > 1 else "")
                    
                    user = User.objects.create_user(
                        username=clean_phone,
                        phone=clean_phone,
                        password=generated_password,
                        first_name=resolved_first,
                        last_name=resolved_last,
                        email=clean_email,
                        id_type=id_type_raw,
                        id_number=clean_id_number,
                        is_active=True
                    )
                    
                    # Remplacement Email -> WhatsApp pour l'activation
                    try:
                        from accounts.services.whatsapp_service import send_whatsapp_activation
                        send_whatsapp_activation(user, request)
                    except Exception as e:
                        print("Erreur envoi whatsapp:", e)
                        pass
                        
                    messages.success(request, "Un message WhatsApp d'activation vous a été envoyé. Le billet est réservé.")
                else:
                    user = current_user
                    if clean_email and user:
                        if user.email != clean_email:
                            try:
                                user.email = clean_email
                                user.save(update_fields=['email'])
                            except Exception:
                                pass
                
                # Extraction des accompagnants
                companions_list = []
                for i in range(2, seats + 1):
                    comp_name = request.POST.get(f'traveler_name_{i}', '').strip()
                    comp_id_type = request.POST.get(f'id_type_{i}', 'none').strip()
                    if comp_name:
                        companions_list.append({
                            'name': comp_name,
                            'id_type': comp_id_type
                        })
                
                final_traveler_email = clean_email or (getattr(user, 'email', None) if user else None) or ''

                # Création Réservation avec séparation financière claire
                payment_choice = request.POST.get('payment_choice', 'online')
                total_to_pay = segment_price * seats

                # Si l'utilisateur est authentifié et choisit de régler avec son portefeuille
                if payment_choice == 'wallet' and user and hasattr(user, 'wallet_balance') and user.wallet_balance >= total_to_pay:
                    import uuid
                    import threading
                    from django.utils import timezone
                    
                    user.wallet_balance -= total_to_pay
                    user.save(update_fields=['wallet_balance'])
                    
                    booking = Booking.objects.create(
                        user=user,
                        departure=departure,
                        departure_stop=start_stop,
                        arrival_stop=end_stop,
                        traveler_name=clean_name,
                        traveler_phone=clean_phone,
                        traveler_email=final_traveler_email,
                        id_type=id_type_raw,
                        id_number=clean_id_number,
                        companions=companions_list,
                        seats_reserved=seats,
                        agency_amount=agency_unit_price * seats,
                        platform_fee=platform_unit_fee * seats,
                        total_amount=total_to_pay,
                        payment_status='paid',
                        payment_method='wallet',
                        payment_operator='Portefeuille EasyTravel',
                        transaction_id=f"WLT-BOOK-{uuid.uuid4().hex[:8].upper()}",
                        status='confirmed',
                        paid_at=timezone.now()
                    )
                    
                    departure.available_capacity -= seats
                    departure.save()
                    
                    try:
                        from accounts.services.email_service import send_ticket_confirmation_email
                        email_t = threading.Thread(
                            target=send_ticket_confirmation_email,
                            args=(request, booking),
                            daemon=True
                        )
                        email_t.start()
                    except Exception:
                        pass
                        
                    messages.success(
                        request,
                        f"Félicitations ! Votre billet a été réglé avec succès depuis votre Portefeuille EasyTravel. "
                        f"Montant débité : {int(total_to_pay):,} FCFA. Nouveau solde : {int(user.wallet_balance):,} FCFA."
                    )
                    return redirect(reverse('bookings:confirmation', args=[booking.reference]))
                else:
                    booking = Booking.objects.create(
                        user=user,
                        departure=departure,
                        departure_stop=start_stop,
                        arrival_stop=end_stop,
                        traveler_name=clean_name,
                        traveler_phone=clean_phone,
                        traveler_email=final_traveler_email,
                        id_type=id_type_raw,
                        id_number=clean_id_number,
                        companions=companions_list,
                        seats_reserved=seats,
                        agency_amount=agency_unit_price * seats,
                        platform_fee=platform_unit_fee * seats,
                        total_amount=total_to_pay,
                        payment_status='pending',
                        status='pending'
                    )
                    
                    departure.available_capacity -= seats
                    departure.save()
                    
                    return redirect(reverse('payments:initiate', args=[booking.id]))
                
        except ValidationError as e:
            error = e.message
            messages.error(request, error)
            
    return render(request, 'bookings/book.html', {
        'departure': departure,
        'start_stop': start_stop,
        'end_stop': end_stop,
        'segment_price': segment_price,
        'agency_unit_price': agency_unit_price,
        'platform_unit_fee': platform_unit_fee,
        'form_data': form_data,
        'error': error,
    })

def booking_confirmation(request, reference):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'departure', 'departure__agency', 'departure__vehicle',
            'departure_stop__city', 'arrival_stop__city', 'user'
        ),
        reference=reference
    )
    
    # Vérification IDOR : Bloquer l'accès aux autres utilisateurs
    if booking.user and booking.user != request.user:
        # Autoriser les agents d'agence à consulter les billets
        is_agency_staff = request.user.is_authenticated and (
            request.user.is_staff or 
            request.user.is_superuser or 
            getattr(request.user, 'role', '') in ['manager', 'agent', 'admin']
        )
        if not is_agency_staff:
            messages.error(request, "Accès non autorisé à cette réservation.")
            return redirect('travel:home')
        
    start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
    end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"
    
    # Le QR code officiel d'embarquement n'est généré et délivré QUE si le billet est payé
    if booking.payment_status == 'paid':
        qr_code_url = booking.generate_qr_code_base64(request=request)
    else:
        qr_code_url = None
    
    layout = request.GET.get('layout')
    if layout == 'clean':
        base_template = 'agencies/base_clean_ticket.html'
    else:
        base_template = 'agencies/base_agency.html' if (
            request.user.is_authenticated and getattr(request.user, 'role', '') in ['manager', 'agent', 'admin']
        ) else 'travel/base.html'
    
    return render(request, 'bookings/confirmation.html', {
        'booking': booking,
        'start_city': start_city,
        'end_city': end_city,
        'qr_code_url': qr_code_url,
        'base_template': base_template,
    })


def verify_ticket(request, reference):
    """
    Vue publique officielle de vérification d'un titre de transport EasyTravel.
    Accessible par scan direct du QR Code via smartphone ou par saisie de la référence.
    Affiche la validité de la signature cryptographique et le statut en temps réel du billet.
    """
    booking = get_object_or_404(
        Booking.objects.select_related(
            'departure', 'departure__agency', 'departure__vehicle',
            'departure_stop__city', 'arrival_stop__city', 'user'
        ),
        reference=reference
    )
    
    sig = request.GET.get('sig', '').strip()
    is_signature_valid = booking.verify_security_signature(sig)
    
    start_city = booking.departure_stop.city.name if booking.departure_stop and booking.departure_stop.city else "Départ"
    end_city = booking.arrival_stop.city.name if booking.arrival_stop and booking.arrival_stop.city else "Arrivée"
    
    # Vérification si l'utilisateur connecté est un agent de la compagnie concernée
    user_agency = getattr(request.user, 'agency', None) if request.user.is_authenticated else None
    is_agency_agent = (
        request.user.is_authenticated and (
            request.user.is_superuser or 
            request.user.is_staff or 
            (user_agency and user_agency == booking.departure.agency) or
            getattr(request.user, 'role', '') in ['manager', 'agent', 'admin']
        )
    )
    
    # Embarquement direct depuis l'interface mobile si l'agent de quai clique sur "Valider l'embarquement"
    if request.method == 'POST' and is_agency_agent:
        action = request.POST.get('action')
        if action == 'board':
            if booking.payment_status != 'paid':
                messages.error(request, f"Impossible d'embarquer : le billet {booking.reference} n'est pas encore payé.")
            elif booking.status == 'cancelled':
                messages.error(request, f"Impossible d'embarquer : ce billet a été annulé.")
            elif booking.status == 'boarded':
                messages.warning(request, f"Le passager {booking.traveler_name} a déjà été composté / embarqué.")
            else:
                booking.status = 'boarded'
                booking.save(update_fields=['status'])
                messages.success(request, f"✓ Passager {booking.traveler_name} ({booking.seats_reserved} place) embarqué avec succès !")
            
            redirect_url = reverse('bookings:verify_ticket', kwargs={'reference': booking.reference})
            if sig:
                redirect_url += f"?sig={sig}"
            return redirect(redirect_url)

    is_paid = (booking.payment_status == 'paid')
    is_boarded = (booking.status == 'boarded')
    is_cancelled = (booking.status == 'cancelled')
    is_valid_for_boarding = is_paid and not is_boarded and not is_cancelled and not booking.is_expired

    return render(request, 'bookings/verify_ticket.html', {
        'booking': booking,
        'start_city': start_city,
        'end_city': end_city,
        'sig': sig,
        'is_signature_valid': is_signature_valid,
        'is_paid': is_paid,
        'is_boarded': is_boarded,
        'is_cancelled': is_cancelled,
        'is_valid_for_boarding': is_valid_for_boarding,
        'is_agency_agent': is_agency_agent,
    })

def booking_feedback_view(request, reference):
    """Vue pour recueillir la note de satisfaction du voyageur"""
    from .models import BookingFeedback
    booking = get_object_or_404(Booking, reference=reference)
    
    # Vérification IDOR : Seul le propriétaire peut laisser un avis
    if booking.user and booking.user != request.user:
        messages.error(request, "Accès non autorisé à cette réservation.")
        return redirect('travel:home')
    
    # Si un sondage a déjà été soumis pour ce billet, on affiche un message
    if hasattr(booking, 'feedback'):
        messages.info(request, "Vous avez déjà donné votre avis pour ce voyage, merci !")
        return redirect('travel:home')
        
    if request.method == 'POST':
        try:
            rating = int(request.POST.get('rating', 5))
            if rating < 1 or rating > 5:
                rating = 5
        except ValueError:
            rating = 5
            
        comment = request.POST.get('comment', '').strip()
        
        BookingFeedback.objects.create(
            booking=booking,
            rating=rating,
            comment=comment
        )
        
        messages.success(request, "Merci ! Votre avis a bien été enregistré. Il aidera l'agence à s'améliorer.")
        return redirect('travel:home')
        
    return render(request, 'bookings/feedback.html', {'booking': booking})
