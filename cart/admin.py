from django.contrib import admin
from cart.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'session_key', 'total_quantity', 'line_count', 'created_at', 'updated_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('user__username', 'session_key')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [CartItemInline]


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'cart', 'variant', 'quantity', 'price_at_addition', 'current_unit_price', 'line_total', 'created_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('variant__sku', 'cart__user__username', 'cart__session_key')
    readonly_fields = ('created_at', 'updated_at')
