"""
Automated Unit and Integration Tests for EasyTravel CinetPay Payments.
Tests initiation, checking, IPN webhook handling, and UI redirections.
Uses unittest.mock to guarantee 100% network isolation.
"""

from unittest.mock import patch, MagicMock
from datetime import date, time
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from accounts.models import User
from agencies.models import Agency
from travel.models import City, Line, LineStop, Departure
from bookings.models import Booking
from payments.services.cinetpay import CinetPayService
from payments.services.saspay import SasPayService


class CinetPayPaymentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='voyageur_test', 
            password='secretpassword123',
            first_name='Jean',
            last_name='Kamga',
            email='jean.kamga@test.cm',
            phone='699001122'
        )
        self.owner = User.objects.create_user(username='agency_admin', password='password', email='agency_admin@test.cm')
        self.agency = Agency.objects.create(name="Cameroon Express", owner=self.owner)
        self.city_douala = City.objects.create(name="Douala")
        self.city_yaounde = City.objects.create(name="Yaoundé")
        self.line = Line.objects.create(agency=self.agency, name="Douala - Yaoundé")
        self.stop_1 = LineStop.objects.create(line=self.line, city=self.city_douala, stop_order=1, price_from_start=0)
        self.stop_2 = LineStop.objects.create(line=self.line, city=self.city_yaounde, stop_order=2, price_from_start=5000)
        self.departure = Departure.objects.create(
            agency=self.agency,
            line=self.line,
            date=date.today(),
            time=time(7, 0),
            available_capacity=45
        )
        self.booking = Booking.objects.create(
            user=self.user,
            departure=self.departure,
            departure_stop=self.stop_1,
            arrival_stop=self.stop_2,
            traveler_name="Jean Kamga",
            traveler_phone="237699001122",
            traveler_email="jean.kamga@test.cm",
            id_type="CNI",
            id_number="112233445",
            seats_reserved=1,
            total_amount=5000,
            status='confirmed',
            payment_status='pending',
            payment_method='online'
        )

    def test_service_unconfigured_behavior(self):
        """When keys are missing, service safely aborts without crashing."""
        service = CinetPayService()
        service.site_id = ""
        service.api_key = ""
        self.assertFalse(service.is_configured())
        success, result, tx_id = service.initiate_payment(
            booking=self.booking,
            return_url="http://test/return",
            notify_url="http://test/notify"
        )
        self.assertFalse(success)
        self.assertIn("non configurée", result["message"])

    @patch('payments.services.cinetpay.requests.post')
    def test_service_initiate_payment_success(self, mock_post):
        """Simulates successful checkout creation on CinetPay API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "201",
            "message": "CREATED",
            "data": {
                "payment_token": "token_cinetpay_abc123",
                "payment_url": "https://checkout.cinetpay.com/pay/token_cinetpay_abc123"
            }
        }
        mock_post.return_value = mock_response

        service = CinetPayService()
        service.site_id = "123456"
        service.api_key = "test_api_key"

        success, result, tx_id = service.initiate_payment(
            booking=self.booking,
            return_url="http://test/return",
            notify_url="http://test/notify"
        )

        self.assertTrue(success)
        self.assertEqual(result["payment_url"], "https://checkout.cinetpay.com/pay/token_cinetpay_abc123")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.transaction_id, tx_id)
        self.assertEqual(self.booking.payment_operator, "cinetpay")

    @patch('payments.services.cinetpay.requests.post')
    def test_service_check_and_update_booking_accepted(self, mock_post):
        """Simulates verified CinetPay payment confirmation (status ACCEPTED)."""
        tx_id = "ET-TX-TEST-999"
        self.booking.transaction_id = tx_id
        self.booking.save()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "00",
            "message": "SUCCES",
            "data": {
                "amount": 5000,
                "currency": "XAF",
                "status": "ACCEPTED",
                "operator_id": "ORANGE_MONEY_CMR",
                "payment_method": "OM"
            }
        }
        mock_post.return_value = mock_response

        service = CinetPayService()
        service.site_id = "123456"
        service.api_key = "test_api_key"

        updated, b, msg = service.verify_and_update_booking(tx_id)

        self.assertTrue(updated)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.payment_status, 'paid')
        self.assertEqual(self.booking.payment_operator, 'ORANGE_MONEY_CMR')
        self.assertIsNotNone(self.booking.paid_at)

    @patch.object(SasPayService, 'is_configured', return_value=True)
    @patch.object(SasPayService, 'initiate_payment')
    def test_view_initiate_booking_payment_redirects_to_cinetpay(self, mock_initiate, mock_configured):
        """When simulation mode is disabled, view initiates payment and redirects user to live payment URL."""
        from django.test import override_settings
        with override_settings(PAYMENT_SIMULATION_MODE=False):
            mock_initiate.return_value = (
                True, 
                {"payment_url": "https://checkout.saspay.me/pay/xyz789"}, 
                "ET-TX-1-ABCD"
            )
            self.client.login(username='voyageur_test', password='secretpassword123')
            url = reverse('payments:initiate', kwargs={'booking_id': self.booking.id})
            response = self.client.get(url)

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "https://checkout.saspay.me/pay/xyz789")

    @override_settings(PAYMENT_SIMULATION_MODE=True)
    def test_view_initiate_redirects_to_simulation(self):
        """When simulation mode is enabled, initiate view redirects to sandbox checkout."""
        self.client.login(username='voyageur_test', password='secretpassword123')
        url = reverse('payments:initiate', kwargs={'booking_id': self.booking.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        expected_url = reverse('payments:simulation_checkout', kwargs={'booking_id': self.booking.id})
        self.assertEqual(response.url, expected_url)

    def test_simulation_checkout_view(self):
        """Simulation checkout view renders properly with booking details."""
        self.client.login(username='voyageur_test', password='secretpassword123')
        url = reverse('payments:simulation_checkout', kwargs={'booking_id': self.booking.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.booking.reference)
        self.assertContains(response, "MTN Mobile Money")
        self.assertContains(response, "Orange Money")

    @override_settings(PAYMENT_SIMULATION_MODE=True)
    def test_simulation_process_success(self):
        """Simulation process handles successful payment and updates booking to paid."""
        self.client.login(username='voyageur_test', password='secretpassword123')
        url = reverse('payments:simulation_process', kwargs={'booking_id': self.booking.id})
        response = self.client.post(url, data={
            'action': 'success',
            'payment_method': 'orange_money',
            'phone': '699112233',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('bookings:confirmation', kwargs={'reference': self.booking.reference}))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.payment_status, 'paid')
        self.assertEqual(self.booking.payment_method, 'orange_money')
        self.assertIn('Orange Money', self.booking.payment_operator)
        self.assertTrue(self.booking.transaction_id.startswith('SIM-TX-'))
        self.assertIsNotNone(self.booking.paid_at)

    @override_settings(PAYMENT_SIMULATION_MODE=True)
    def test_simulation_process_failure(self):
        """Simulation process handles failed payment attempt and marks status as failed."""
        self.client.login(username='voyageur_test', password='secretpassword123')
        url = reverse('payments:simulation_process', kwargs={'booking_id': self.booking.id})
        response = self.client.post(url, data={
            'action': 'fail',
            'payment_method': 'mtn_momo',
            'phone': '677112233',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('bookings:confirmation', kwargs={'reference': self.booking.reference}))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.payment_status, 'failed')

    @patch.object(SasPayService, 'verify_webhook_signature', return_value=True)
    @patch.object(SasPayService, 'verify_and_update_booking_webhook')
    def test_view_cinetpay_notification_ipn_webhook(self, mock_update, mock_sig):
        """Validates that SasPay IPN webhook triggers verification and returns 200."""
        import json
        mock_update.return_value = (True, self.booking, "Paiement validé avec succès.")
        url = reverse('payments:notification')

        payload = {
            'event': 'transaction.success',
            'data': {
                'transaction_id': 'ET-TX-TEST-IPN',
                'status': 'SUCCESS',
                'metadata': {'project': 'easytravel'}
            }
        }
        response = self.client.post(
            url, 
            data=json.dumps(payload), 
            content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE='valid_sig',
            HTTP_X_WEBHOOK_TIMESTAMP='1700000000'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')

    @patch.object(SasPayService, 'verify_payment')
    @patch.object(SasPayService, 'verify_and_update_booking_webhook')
    def test_view_cinetpay_return_redirects_to_confirmation(self, mock_update, mock_verify):
        """User returning from SasPay is safely redirected to booking confirmation page."""
        self.booking.payment_status = 'paid'
        self.booking.save()
        mock_verify.return_value = (True, {'transaction_id': 'ET-TX-RETURN-1', 'status': 'SUCCESS'})
        mock_update.return_value = (True, self.booking, "Paiement validé avec succès.")

        url = f"{reverse('payments:return')}?transaction_id=ET-TX-RETURN-1"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        expected_url = reverse('bookings:confirmation', kwargs={'reference': self.booking.reference})
        self.assertEqual(response.url, expected_url)

    @override_settings(PAYMENT_SIMULATION_MODE=True)
    def test_payment_transaction_model_and_simulation(self):
        """Verifies that PaymentTransaction model correctly stores payment lifecycle."""
        from payments.models import PaymentTransaction
        
        self.client.force_login(self.user)
        url = reverse('payments:simulation_process', kwargs={'booking_id': self.booking.id})
        
        # Test simulation success
        response = self.client.post(url, {
            'action': 'success',
            'payment_method': 'orange_money',
            'phone': '699001122'
        })
        self.assertEqual(response.status_code, 302)
        
        tx = PaymentTransaction.objects.filter(booking=self.booking, status='success').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.provider, 'simulation')
        self.assertEqual(tx.payment_method, 'orange_money')
        self.assertEqual(tx.amount, self.booking.total_amount)
        self.assertEqual(tx.currency, 'XAF')
        self.assertIsNotNone(tx.completed_at)
        self.assertIn('SIMULATION', str(tx).upper())

