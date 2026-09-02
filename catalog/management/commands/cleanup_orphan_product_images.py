import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from catalog.models import ProductImage
from catalog.product_image_service import delete_object, normalize_product_images


logger = logging.getLogger("catalog.product_images")


class Command(BaseCommand):
    help = "Delete old unattached product image objects and retry pending R2 deletions."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours < 1:
            raise CommandError("--hours must be at least 1")
        cutoff = timezone.now() - timedelta(hours=hours)
        candidates = ProductImage.objects.filter(
            Q(
                product__isnull=True,
                created_at__lt=cutoff,
                upload_status__in=(
                    ProductImage.UploadStatus.PENDING,
                    ProductImage.UploadStatus.TEMPORARY,
                ),
            )
            | Q(upload_status=ProductImage.UploadStatus.DELETION_PENDING)
        ).order_by("pk")

        deleted = 0
        failed = 0
        for image in candidates.iterator():
            if options["dry_run"]:
                self.stdout.write(f"would delete product image #{image.pk}")
                continue
            try:
                product = image.product
                if image.image:
                    delete_object(image.image.name)
                if image.thumbnail:
                    delete_object(image.thumbnail.name)
                image.delete()
                if product:
                    normalize_product_images(product)
                deleted += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "Orphan product image cleanup failed image_id=%s key=%s",
                    image.pk,
                    image.image.name if image.image else "",
                )
                self.stderr.write(f"product image #{image.pk}: {type(exc).__name__}")

        self.stdout.write(
            self.style.SUCCESS(
                f"orphan cleanup complete: deleted={deleted} failed={failed}"
            )
        )
