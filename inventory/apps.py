from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'
    verbose_name = 'Quản lý Kho hàng'

    def ready(self):
        import inventory.signals  # noqa: F401

