from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import SiteSettings
from orders.models import Order


class Command(BaseCommand):
    help = "保存期間経過後、会計記録に紐づかない取消注文のPIIを匿名化します。既定はdry-runです。"

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--before", help="ISO date。省略時はSiteSettingsの保存日数")

    def handle(self, *args, **options):
        if options["before"]:
            cutoff = timezone.datetime.fromisoformat(options["before"])
            cutoff = timezone.make_aware(cutoff) if timezone.is_naive(cutoff) else cutoff
        else:
            cutoff = timezone.now() - timedelta(days=SiteSettings.load().customer_data_retention_days)
        queryset = Order.objects.filter(created_at__lt=cutoff, status=Order.Status.CANCELLED, payments__isnull=True).distinct()
        self.stdout.write(f"eligible={queryset.count()} cutoff={cutoff.isoformat()}")
        if not options["execute"]:
            self.stdout.write(self.style.WARNING("dry-run: --executeで匿名化します。付款・会計記録のある注文は対象外です。"))
            return
        with transaction.atomic():
            for order in queryset.select_for_update():
                order.customer_name = order.recipient_name = "已匿名化"
                order.phone = ""
                order.email = f"anonymized-{order.public_id}@invalid.local"
                order.shipping_information = order.street_address = order.delivery_note = order.customer_note = ""
                order.postal_code = order.city = order.district = ""
                order.save()
        self.stdout.write(self.style.SUCCESS(f"anonymized={queryset.count()}"))
