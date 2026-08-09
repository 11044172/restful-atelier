from django.db import migrations, models


def enable_existing_checkout(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.update(checkout_enabled=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="checkout_enabled",
            field=models.BooleanField(default=True, verbose_name="注文受付を有効化"),
        ),
        migrations.RunPython(enable_existing_checkout, migrations.RunPython.noop),
    ]
