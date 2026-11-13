from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_paymentreconciliation'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='paymentreconciliation',
            name='unique_open_reconciliation_per_payment',
        ),
        migrations.AddConstraint(
            model_name='paymentreconciliation',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status__in', ['OPEN', 'ESCALATED'])),
                fields=('payment',),
                name='unique_unresolved_reconciliation_per_payment',
            ),
        ),
    ]

