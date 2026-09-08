from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0007_add_ecpay_payment_fields"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="orderauditlog",
            options={
                "ordering": ("-created_at", "-pk"),
                "verbose_name": "訂單稽核紀錄",
                "verbose_name_plural": "訂單稽核紀錄",
            },
        ),
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待處理"),
                    ("awaiting_confirmation", "等待確認"),
                    ("confirmed", "已確認"),
                    ("failed", "失敗"),
                    ("cancelled", "已取消"),
                    ("partially_refunded", "部分退款"),
                    ("refunded", "全額退款"),
                    ("overpaid", "溢付"),
                    ("chargeback", "爭議款／拒付"),
                ],
                default="pending",
                max_length=32,
                verbose_name="付款狀態",
            ),
        ),
    ]
