from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_alter_sitesettings_returns_contact"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="business_address",
            field=models.CharField(blank=True, max_length=300, verbose_name="營業地址"),
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="business_legal_name",
            field=models.CharField(blank=True, max_length=180, verbose_name="商家法定名稱"),
        ),
    ]
