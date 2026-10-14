from django.contrib import admin
from .models import PaymentTransaction


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'transaction_id',
        'provider',
        'amount',
        'currency',
        'status',
        'booking',
        'cart',
        'user',
        'created_at',
        'completed_at',
    )
    list_filter = ('provider', 'status', 'currency', 'created_at')
    search_fields = (
        'transaction_id',
        'provider_reference',
        'booking__reference',
        'cart__reference',
        'user__username',
        'user__phone',
        'user__email',
    )
    readonly_fields = (
        'transaction_id',
        'provider',
        'provider_reference',
        'amount',
        'currency',
        'booking',
        'cart',
        'user',
        'payment_method',
        'status',
        'raw_response',
        'error_message',
        'created_at',
        'updated_at',
        'completed_at',
    )
    ordering = ('-created_at',)
