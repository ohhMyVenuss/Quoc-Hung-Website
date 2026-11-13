from django.contrib import admin
from .models import CustomerProfile


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'phone', 'default_address')
    search_fields = ('user__username', 'user__email', 'phone')
    list_select_related = ('user',)
