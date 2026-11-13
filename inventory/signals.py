from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='catalog.ProductVariant')
def auto_create_variant_inventory(sender, instance, created, raw=False, **kwargs):
    if raw:
        return

    if created:
        from inventory.models import Inventory
        Inventory.objects.get_or_create(
            variant=instance,
            defaults={
                'physical_sellable': 0,
                'reserved': 0,
                'min_threshold': 0,
            },
        )

