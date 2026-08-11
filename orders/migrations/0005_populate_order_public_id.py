import uuid

from django.db import migrations


def populate_public_ids(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.filter(public_id__isnull=True).iterator(chunk_size=500):
        order.public_id = uuid.uuid4()
        order.save(update_fields=("public_id",))


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_notificationoutbox_orderauditlog_policyacceptance_and_more")]
    operations = [migrations.RunPython(populate_public_ids, migrations.RunPython.noop)]
