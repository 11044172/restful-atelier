import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0008_sitesettings_contact_lead_sitesettings_contact_title"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="DirectImageUpload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_key", models.CharField(max_length=255, unique=True)),
                ("category", models.CharField(max_length=80)),
                ("original_filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=64)),
                ("file_size", models.PositiveBigIntegerField()),
                ("width", models.PositiveIntegerField()),
                ("height", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("pending", "等待上傳"), ("ready", "已確認"), ("attached", "已使用"), ("deletion_pending", "等待清理")], default="pending", max_length=24)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("uploaded_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="direct_image_uploads", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name="directimageupload", index=models.Index(fields=["status", "created_at"], name="core_direct_status_f1387c_idx")),
    ]
