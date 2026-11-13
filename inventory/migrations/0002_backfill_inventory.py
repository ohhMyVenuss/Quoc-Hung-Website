from django.db import migrations


def backfill_existing_variants(apps, schema_editor):
    ProductVariant = apps.get_model('catalog', 'ProductVariant')
    Inventory = apps.get_model('inventory', 'Inventory')

    for variant in ProductVariant.objects.all():
        if not Inventory.objects.filter(variant=variant).exists():
            Inventory.objects.create(
                variant=variant,
                physical_sellable=0,
                reserved=0,
                min_threshold=0,
            )


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
        ('catalog', '0005_product_variant'),
    ]

    operations = [
        migrations.RunPython(backfill_existing_variants, reverse_backfill),
    ]

