from datetime import date, time, timedelta
from django.test import TestCase
from accounts.models import User
from agencies.models import Agency
from travel.models import City, Line, LineStop, Departure, Vehicle
from bookings.models import Booking

class BookingModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='traveler1', password='pw', phone='699112233', email='traveler1@test.cm')
        self.owner = User.objects.create_user(username='agencyowner2', password='pw', email='owner2@test.cm')
        self.agency = Agency.objects.create(name="Voyage Test", owner=self.owner)
        self.city_a = City.objects.create(name="Douala")
        self.city_b = City.objects.create(name="Bafoussam")
        self.line = Line.objects.create(agency=self.agency, name="Ligne Ouest")
        self.stop_1 = LineStop.objects.create(line=self.line, city=self.city_a, stop_order=1, price_from_start=0)
        self.stop_2 = LineStop.objects.create(line=self.line, city=self.city_b, stop_order=2, price_from_start=5000)
        self.departure = Departure.objects.create(
            agency=self.agency,
            line=self.line,
            date=date.today() + timedelta(days=2),
            time=time(14, 0),
            available_capacity=30
        )

    def test_booking_creation_and_reference(self):
        booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Samuel Eto'o",
            traveler_phone="699112233",
            traveler_email="samuel@test.com",
            id_type="CNI",
            id_number="101010101",
            seats_reserved=2,
            total_amount=10000,
            status='confirmed',
            payment_status='paid'
        )
        self.assertTrue(booking.reference.startswith("ET-"))
        self.assertEqual(len(booking.reference), 11)  # ET- + 8 chars
        self.assertIsNotNone(booking.paid_at)
        self.assertEqual(booking.seats_reserved, 2)
        self.assertEqual(booking.total_amount, 10000)

    def test_booking_string_representation(self):
        booking = Booking.objects.create(
            departure=self.departure,
            traveler_name="Roger Milla",
            traveler_phone="677001122",
            traveler_email="roger@test.com",
            id_type="CNI",
            id_number="102938475",
            seats_reserved=1,
            total_amount=5000
        )
        self.assertIn("Roger Milla", str(booking))
        self.assertIn(booking.reference, str(booking))

    def test_unauthenticated_booking_creates_account_and_requires_email_activation(self):
        from django.test import Client
        from django.urls import reverse
        from django.core import mail
        from accounts.services.email_service import generate_activation_link

        client = Client()
        mail.outbox = []

        initial_capacity = self.departure.available_capacity
        url = f"{reverse('bookings:book', kwargs={'departure_id': self.departure.id})}?start={self.stop_1.id}&end={self.stop_2.id}"

        response = client.post(url, {
            'start_id': self.stop_1.id,
            'end_id': self.stop_2.id,
            'name': 'Rigobert Song',
            'phone': '699112233',
            'email': 'rigobert.song@mslogitech.cm',
            'id_type': 'CNI',
            'id_number': '102938475',
            'seats': 2,
            'password': 'SongPassword2026!',
            'password_confirm': 'SongPassword2026!'
        })

        # Doit rediriger vers le paiement ou confirmation
        self.assertEqual(response.status_code, 302)
        
        # Vérifier que le compte utilisateur a été créé avec is_active=True
        new_user = User.objects.get(email='rigobert.song@mslogitech.cm')
        self.assertTrue(new_user.is_active)

        # Vérifier que la réservation a été créée en statut 'pending'
        booking = Booking.objects.get(user=new_user)
        self.assertEqual(booking.status, 'pending')
        self.assertEqual(booking.seats_reserved, 2)

        # La capacité est déduite immédiatement
        self.departure.refresh_from_db()
        self.assertEqual(self.departure.available_capacity, initial_capacity - 2)

    def test_qr_security_signature_and_generation(self):
        booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Samuel Eto'o",
            traveler_phone="699112233",
            traveler_email="samuel@test.com",
            id_type="cni",
            id_number="101010101",
            seats_reserved=2,
            total_amount=10000,
            status='confirmed',
            payment_status='paid'
        )
        sig = booking.get_security_signature()
        self.assertTrue(sig)
        self.assertTrue(booking.verify_security_signature(sig))
        self.assertFalse(booking.verify_security_signature("fake_sig_123"))
        
        # Test generation URL & QR Code Base64
        verify_url = booking.get_verification_url()
        self.assertIn(booking.reference, verify_url)
        self.assertIn(f"sig={sig}", verify_url)
        
        qr_b64 = booking.generate_qr_code_base64()
        self.assertTrue(qr_b64.startswith("data:image/png;base64,"))

    def test_verify_ticket_view(self):
        from django.test import Client
        from django.urls import reverse
        client = Client()
        
        booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Samuel Eto'o",
            traveler_phone="699112233",
            traveler_email="samuel@test.com",
            id_type="cni",
            id_number="101010101",
            seats_reserved=2,
            total_amount=10000,
            status='confirmed',
            payment_status='paid'
        )
        sig = booking.get_security_signature()
        url = reverse('bookings:verify_ticket', kwargs={'reference': booking.reference})
        
        # Avec signature valide
        response = client.get(f"{url}?sig={sig}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_signature_valid'])
        self.assertTrue(response.context['is_valid_for_boarding'])

        # Avec fausse signature
        response = client.get(f"{url}?sig=invalid_signature")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_signature_valid'])

    def test_ajax_scan_ticket_blocks_unpaid_booking(self):
        from django.test import Client
        from django.urls import reverse
        client = Client()
        client.force_login(self.owner)
        
        # Création d'un billet non payé
        booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Passager Fraudeur",
            traveler_phone="699112233",
            traveler_email="fraude@test.com",
            seats_reserved=1,
            total_amount=5000,
            status='pending',
            payment_status='pending'
        )
        scan_url = reverse('agencies:ajax_scan_ticket')
        verify_url = booking.get_verification_url()
        
        # Tentative de scan du QR code non payé
        response = client.post(scan_url, {'payload': verify_url})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['status'], 'error')
        self.assertIn("n'est PAS réglé", data['message'])
        
        # Le statut ne doit pas être passé à 'boarded'
        booking.refresh_from_db()
        self.assertNotEqual(booking.status, 'boarded')

    def test_ajax_scan_ticket_allows_paid_booking(self):
        from django.test import Client
        from django.urls import reverse
        client = Client()
        client.force_login(self.owner)
        
        # Création d'un billet payé
        booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Passager Honnête",
            traveler_phone="699112233",
            traveler_email="honnete@test.com",
            seats_reserved=1,
            total_amount=5000,
            status='confirmed',
            payment_status='paid'
        )
        scan_url = reverse('agencies:ajax_scan_ticket')
        verify_url = booking.get_verification_url()
        
        # Scan du QR code officiel
        response = client.post(scan_url, {'payload': verify_url})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        
        # Le statut passe bien à 'boarded'
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'boarded')
        
        # Un second scan doit signaler qu'il a déjà embarqué
        response2 = client.post(scan_url, {'payload': verify_url})
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertEqual(data2['status'], 'warning')
        self.assertIn("DÉJÀ embarqué", data2['message'])

    def test_booking_admin_form_and_api(self):
        from bookings.admin import BookingAdminForm
        from django.test import Client
        
        # Test 1 : Validation et auto-complétion du formulaire admin
        form_data = {
            'user': self.user.id,
            'departure': self.departure.id,
            'departure_stop': self.stop_1.id,
            'arrival_stop': self.stop_2.id,
            'seats_reserved': 2,
            'status': 'confirmed',
            'payment_status': 'paid',
        }
        form = BookingAdminForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data
        
        # Le nom, le tél, l'email doivent être automatiquement tirés du User
        self.assertEqual(cleaned['traveler_name'], self.user.username)
        self.assertEqual(cleaned['traveler_phone'], self.user.phone)
        self.assertEqual(cleaned['traveler_email'], self.user.email)
        
        # Le tarif doit être calculé automatiquement
        pricing = self.departure.calculate_pricing(self.stop_1, self.stop_2)
        expected_total = pricing['total_price'] * 2
        self.assertEqual(cleaned['total_amount'], expected_total)
        
        # Test 2 : API User Info
        admin_user = User.objects.create_superuser('admin_test', 'admin@test.cm', 'password123')
        client = Client()
        client.force_login(admin_user)
        
        res_user = client.get(f'/admin/bookings/booking/api/user-info/{self.user.id}/')
        self.assertEqual(res_user.status_code, 200)
        self.assertEqual(res_user.json()['traveler_phone'], self.user.phone)
        
        # Test 3 : API Departure Info
        res_dep = client.get(f'/admin/bookings/booking/api/departure-info/{self.departure.id}/')
        self.assertEqual(res_dep.status_code, 200)
        self.assertEqual(len(res_dep.json()['stops']), 2)
        
        # Test 4 : API Calculate Price
        res_price = client.get(f'/admin/bookings/booking/api/calculate-price/?departure_id={self.departure.id}&start_id={self.stop_1.id}&end_id={self.stop_2.id}&seats=3')
        self.assertEqual(res_price.status_code, 200)
        expected_3_seats = pricing['total_price'] * 3
        self.assertEqual(res_price.json()['total_amount'], round(expected_3_seats, 2))





