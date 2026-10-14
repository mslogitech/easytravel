from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, SystemAlert

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Informations Supplémentaires', {'fields': ('role', 'agency', 'phone', 'profile_picture')}),
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'agency', 'is_active')
    list_filter = ('role', 'agency', 'is_staff', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'phone', 'id_number')

admin.site.register(User, CustomUserAdmin)

@admin.register(SystemAlert)
class SystemAlertAdmin(admin.ModelAdmin):
    list_display = ('title', 'level', 'is_resolved', 'created_at')
    list_filter = ('level', 'is_resolved', 'created_at')
    search_fields = ('title', 'message')
    readonly_fields = ('created_at',)
