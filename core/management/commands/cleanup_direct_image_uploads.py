from datetime import timedelta

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import DirectImageUpload


class Command(BaseCommand):
    help = "List or delete abandoned, unattached admin direct-image uploads."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--delete", action="store_true", help="Delete matched R2 objects and tracking rows.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=max(1, options["older_than_hours"]))
        queryset = DirectImageUpload.objects.filter(
            status__in=(DirectImageUpload.Status.PENDING, DirectImageUpload.Status.READY, DirectImageUpload.Status.DELETION_PENDING),
            created_at__lt=cutoff,
        ).order_by("pk")
        count = queryset.count()
        if not options["delete"]:
            self.stdout.write(f"{count} abandoned upload(s) found; dry run only.")
            return
        deleted = 0
        for upload in queryset.iterator():
            try:
                default_storage.delete(upload.object_key)
            except Exception as exc:
                upload.status = DirectImageUpload.Status.DELETION_PENDING
                upload.save(update_fields=("status", "updated_at"))
                self.stderr.write(f"Could not delete upload {upload.pk}: {exc}")
            else:
                upload.delete()
                deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} abandoned upload(s)."))
