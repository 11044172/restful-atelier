# Generated for the existing Cloudflare R2 direct-upload workflow.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_alter_product_category_alter_product_description_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="productimage",
            name="one_primary_image_per_product",
        ),
        migrations.AlterField(
            model_name="productimage",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="images",
                to="catalog.product",
                verbose_name="商品",
            ),
        ),
        migrations.AddField(
            model_name="productimage",
            name="content_type",
            field=models.CharField(blank=True, max_length=64, verbose_name="Content-Type"),
        ),
        migrations.AddField(
            model_name="productimage",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
                verbose_name="建立時間",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productimage",
            name="file_size",
            field=models.PositiveBigIntegerField(blank=True, null=True, verbose_name="檔案大小"),
        ),
        migrations.AddField(
            model_name="productimage",
            name="original_filename",
            field=models.CharField(blank=True, max_length=255, verbose_name="原始檔名"),
        ),
        migrations.AddField(
            model_name="productimage",
            name="upload_session",
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name="上傳工作階段"),
        ),
        migrations.AddField(
            model_name="productimage",
            name="upload_status",
            field=models.CharField(
                choices=[
                    ("pending", "上傳待確認"),
                    ("temporary", "暫存"),
                    ("attached", "已連結商品"),
                    ("deletion_pending", "等待刪除"),
                ],
                default="attached",
                max_length=24,
                verbose_name="上傳狀態",
            ),
        ),
        migrations.AddField(
            model_name="productimage",
            name="uploaded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="uploaded_product_images",
                to=settings.AUTH_USER_MODEL,
                verbose_name="上傳者",
            ),
        ),
        migrations.AddConstraint(
            model_name="productimage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_primary", True), ("upload_status", "attached")),
                fields=("product",),
                name="one_primary_image_per_product",
            ),
        ),
        migrations.AddIndex(
            model_name="productimage",
            index=models.Index(
                fields=["product", "upload_status", "sort_order"],
                name="catalog_pi_product_5f29_idx",
            ),
        ),
    ]
