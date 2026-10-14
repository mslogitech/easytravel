"""
URL patterns for EasyTravel Payments.
"""

from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path('payer/<int:booking_id>/', views.initiate_booking_payment, name='initiate'),
    path('payer/portefeuille/<int:booking_id>/', views.pay_with_wallet, name='pay_wallet'),
    path('payer-panier/<int:cart_id>/', views.initiate_cart_payment, name='initiate_cart'),
    path('simulation/<int:booking_id>/', views.simulation_checkout, name='simulation_checkout'),
    path('simulation/<int:booking_id>/process/', views.simulation_process, name='simulation_process'),
    path('saspay/notification/', views.saspay_notification, name='notification'),
    path('saspay/retour/', views.saspay_return, name='return'),
]

