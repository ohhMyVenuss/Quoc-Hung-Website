from django.contrib import admin
from accounts.permissions import is_inventory_staff
from inventory.models import Inventory, InventoryTransaction, InventoryAdjustment


def parse_positive_id(val, max_digits=12):
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or len(val) > max_digits:
        return None
    if not (val.isascii() and val.isdigit()):
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'variant_sku',
        'product_name',
        'physical_sellable',
        'reserved',
        'available_display',
        'min_threshold',
        'stock_status_display',
        'updated_at',
    )
    list_filter = (
        'variant__is_active',
        'variant__product__category',
        'variant__product__is_active',
    )
    search_fields = (
        'variant__sku',
        'variant__product__name',
    )
    readonly_fields = (
        'variant',
        'physical_sellable',
        'reserved',
        'available_display',
        'updated_at',
    )
    fields = (
        'variant',
        'physical_sellable',
        'reserved',
        'available_display',
        'min_threshold',
        'updated_at',
    )
    ordering = ('id',)

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.variant.sku if obj.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        return obj.variant.product.name if obj.variant and obj.variant.product else '-'

    @admin.display(description='Có thể bán (Available)')
    def available_display(self, obj):
        return obj.available

    @admin.display(description='Trạng thái kho')
    def stock_status_display(self, obj):
        return obj.stock_status_label

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def has_view_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def get_search_results(self, request, queryset, search_term):
        base_queryset = queryset
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        term = (search_term or '').strip()
        if term:
            clean_code = term.upper()
            product_id = None
            if clean_code.startswith('SP'):
                product_id = parse_positive_id(clean_code[2:])
            else:
                product_id = parse_positive_id(term)
            if product_id is not None:
                queryset = queryset | base_queryset.filter(variant__product__id=product_id)
                may_have_duplicates = True
        return queryset, may_have_duplicates

    def save_model(self, request, obj, form, change):
        if change and obj.pk:
            orig = Inventory.objects.get(pk=obj.pk)
            obj.variant = orig.variant
            obj.physical_sellable = orig.physical_sellable
            obj.reserved = orig.reserved
        super().save_model(request, obj, form, change)


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'variant_sku',
        'product_name',
        'transaction_type_badge',
        'quantity_delta_display',
        'physical_before',
        'physical_after',
        'performed_by_display',
        'request_key_short',
    )
    list_filter = (
        'transaction_type',
        'created_at',
    )
    search_fields = (
        'inventory__variant__sku',
        'inventory__variant__product__name',
        'performed_by__username',
        'request_key',
    )
    readonly_fields = (
        'inventory',
        'transaction_type',
        'quantity_delta',
        'physical_before',
        'physical_after',
        'performed_by',
        'created_at',
        'request_key',
    )
    fields = (
        'inventory',
        'transaction_type',
        'quantity_delta',
        'physical_before',
        'physical_after',
        'performed_by',
        'created_at',
        'request_key',
    )
    ordering = ('-created_at', '-id')

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.inventory.variant.sku if obj.inventory and obj.inventory.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        if obj.inventory and obj.inventory.variant and obj.inventory.variant.product:
            return obj.inventory.variant.product.name
        return '-'

    @admin.display(description='Loại giao dịch')
    def transaction_type_badge(self, obj):
        return obj.transaction_type_display

    @admin.display(description='Biến động')
    def quantity_delta_display(self, obj):
        return f"+{obj.quantity_delta}" if obj.quantity_delta > 0 else str(obj.quantity_delta)

    @admin.display(description='Người thực hiện')
    def performed_by_display(self, obj):
        return obj.performed_by.username if obj.performed_by else 'Hệ thống'

    @admin.display(description='Mã yêu cầu (UUID)')
    def request_key_short(self, obj):
        return str(obj.request_key)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'variant_sku',
        'product_name',
        'old_quantity',
        'new_quantity',
        'delta_display',
        'reason_short',
        'adjusted_by_display',
        'transaction_key',
    )
    list_filter = (
        'created_at',
    )
    search_fields = (
        'inventory__variant__sku',
        'inventory__variant__product__name',
        'adjusted_by__username',
        'reason',
        'transaction__request_key',
    )
    readonly_fields = (
        'inventory',
        'old_quantity',
        'new_quantity',
        'reason',
        'adjusted_by',
        'transaction',
        'created_at',
    )
    fields = (
        'inventory',
        'old_quantity',
        'new_quantity',
        'reason',
        'adjusted_by',
        'transaction',
        'created_at',
    )
    ordering = ('-created_at', '-id')

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.inventory.variant.sku if obj.inventory and obj.inventory.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        if obj.inventory and obj.inventory.variant and obj.inventory.variant.product:
            return obj.inventory.variant.product.name
        return '-'

    @admin.display(description='Biến động')
    def delta_display(self, obj):
        delta = obj.quantity_delta
        return f"+{delta}" if delta > 0 else str(delta)

    @admin.display(description='Lý do')
    def reason_short(self, obj):
        if len(obj.reason) > 50:
            return obj.reason[:47] + '...'
        return obj.reason

    @admin.display(description='Người điều chỉnh')
    def adjusted_by_display(self, obj):
        return obj.adjusted_by.username if obj.adjusted_by else '-'

    @admin.display(description='Mã giao dịch')
    def transaction_key(self, obj):
        return str(obj.transaction.request_key) if obj.transaction else '-'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions
