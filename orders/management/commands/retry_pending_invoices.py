from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from orders.ecpay_invoice import issue_invoice_safe
from orders.models import Invoice


class Command(BaseCommand):
    help = "安全重試尚未開立的 ECPay 電子發票（已開立發票永不重送 Issue）。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--invoice-id", type=int)

    def handle(self, *args, **options):
        stale_before = timezone.now() - timedelta(minutes=15)
        queryset = Invoice.objects.filter(
            Q(status__in=(Invoice.Status.PENDING, Invoice.Status.FAILED))
            | Q(status=Invoice.Status.ISSUING, last_attempt_at__lte=stale_before)
        )
        if options["invoice_id"]:
            queryset = queryset.filter(pk=options["invoice_id"])
        invoice_ids = list(queryset.order_by("created_at").values_list("pk", flat=True)[: max(1, options["limit"])])
        issued = 0
        for invoice_id in invoice_ids:
            result = issue_invoice_safe(invoice_id, allow_retry=True)
            if result.status == Invoice.Status.ISSUED:
                issued += 1
            self.stdout.write(f"invoice={invoice_id} status={result.status} at={timezone.now().isoformat()}")
        self.stdout.write(self.style.SUCCESS(f"processed={len(invoice_ids)} issued={issued}"))
