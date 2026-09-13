import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from content.models import InteriorProjectImage
from content.project_image_service import delete_object, normalize_project_images

logger = logging.getLogger("content.project_images")


class Command(BaseCommand):
    help = "Delete abandoned project gallery uploads and retry pending R2 deletions."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours < 1:
            raise CommandError("--hours must be at least 1")
        cutoff = timezone.now() - timedelta(hours=hours)
        candidates = InteriorProjectImage.objects.filter(
            Q(
                project__isnull=True,
                created_at__lt=cutoff,
                upload_status__in=(
                    InteriorProjectImage.UploadStatus.PENDING,
                    InteriorProjectImage.UploadStatus.TEMPORARY,
                ),
            )
            | Q(upload_status=InteriorProjectImage.UploadStatus.DELETION_PENDING)
        ).order_by("pk")

        deleted = failed = 0
        for image in candidates.iterator():
            if options["dry_run"]:
                self.stdout.write(f"would delete project image #{image.pk}")
                continue
            try:
                project = image.project
                if image.image:
                    delete_object(image.image.name)
                image.delete()
                if project:
                    normalize_project_images(project)
                deleted += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "orphan_project_image_cleanup_failed image_id=%s key=%s",
                    image.pk,
                    image.image.name if image.image else "",
                )
                self.stderr.write(f"project image #{image.pk}: {type(exc).__name__}")
        self.stdout.write(
            self.style.SUCCESS(
                f"project image cleanup complete: deleted={deleted} failed={failed}"
            )
        )
