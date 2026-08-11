import time

from django.core.management.base import BaseCommand

from orders.notifications import process_next_outbox


class Command(BaseCommand):
    help = "PostgreSQL-backed durable outboxの通知を処理します。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="現在処理可能な通知を処理して終了")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        processed = 0
        while True:
            job = process_next_outbox()
            if job:
                processed += 1
                self.stdout.write(f"{job.pk}: {job.channel}/{job.event_type} -> {job.status}")
                if processed >= options["limit"]:
                    break
                continue
            if options["once"]:
                break
            time.sleep(max(0.2, options["poll_seconds"]))
        self.stdout.write(self.style.SUCCESS(f"processed={processed}"))
