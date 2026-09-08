from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inquiries", "0002_alter_inquiry_options_alter_inquirycategory_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inquiry",
            name="project_location",
            field=models.CharField(blank=True, max_length=240, verbose_name="案件地點"),
        ),
    ]
