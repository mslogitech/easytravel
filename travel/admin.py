from django.contrib import admin
from .models import City, Line, LineStop, Vehicle, Departure, Driver

class LineStopInline(admin.TabularInline):
    model = LineStop
    extra = 2
    ordering = ('stop_order',)

@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'region', 'is_active')
    list_filter = ('is_active', 'region')
    search_fields = ('name', 'region')

@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ('name', 'agency', 'departure_city', 'arrival_city', 'base_price')
    list_filter = ('agency',)
    search_fields = ('name', 'agency__name')
    inlines = [LineStopInline]

@admin.register(LineStop)
class LineStopAdmin(admin.ModelAdmin):
    list_display = ('line', 'city', 'stop_order', 'price_from_start', 'duration_from_start')
    list_filter = ('line__agency', 'city')
    search_fields = ('city__name', 'line__name')
    ordering = ('line', 'stop_order')

@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('registration', 'agency', 'vehicle_type', 'capacity', 'status')
    list_filter = ('agency', 'status', 'vehicle_type')
    search_fields = ('registration', 'agency__name')

@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'phone', 'agency', 'is_active')
    list_filter = ('agency', 'is_active')
    search_fields = ('first_name', 'last_name', 'phone')

@admin.register(Departure)
class DepartureAdmin(admin.ModelAdmin):
    list_display = ('line', 'agency', 'date', 'time', 'status', 'available_capacity', 'vehicle', 'driver')
    list_filter = ('status', 'agency', 'date')
    list_select_related = ('line', 'agency', 'vehicle', 'driver')
    raw_id_fields = ('line', 'agency', 'vehicle', 'driver')
    search_fields = ('line__name', 'agency__name', 'line__departure_city__name', 'line__arrival_city__name')
