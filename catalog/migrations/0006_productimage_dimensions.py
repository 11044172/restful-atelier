from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_product_image_direct_upload")]
    operations = [
        migrations.AddField(model_name="productimage", name="width", field=models.PositiveIntegerField(blank=True, null=True, verbose_name="寬度")),
        migrations.AddField(model_name="productimage", name="height", field=models.PositiveIntegerField(blank=True, null=True, verbose_name="高度")),
    ]
