from django.db import migrations


def setup_accounts(apps, schema_editor):
    from poulailler_core.auth_setup import ensure_default_accounts
    ensure_default_accounts()


class Migration(migrations.Migration):

    dependencies = [
        ('poulailler_core', '0004_feedinventory_batch_feedinventory_expense'),
    ]

    operations = [
        migrations.RunPython(setup_accounts, reverse_code=migrations.RunPython.noop),
    ]
