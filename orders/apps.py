from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "orders"
    verbose_name = "訂單與付款"

    def ready(self):
        from .ecpay import ECPayProvider
        from .payment_providers import register_provider

        register_provider("ecpay", ECPayProvider())
