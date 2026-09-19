import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from catalog.models import Product

REQUIRED_COLUMNS = {"product_id", "current_sku", "current_description"}
REFERENCE_FIELDS = {
    "name": lambda product: product.name,
    "category": lambda product: product.category.name if product.category_id else "",
    "subcategory": lambda product: product.subcategory,
    "maker": lambda product: product.maker,
    "series": lambda product: product.series,
    "short_description": lambda product: product.short_description,
}
SUSPICIOUS_PATTERNS = (
    ("TEST-", re.compile(r"(?i)(?:^|[^a-z0-9])test-")),
    ("test", re.compile(r"(?i)(?:^|[^a-z0-9])test(?:[^a-z0-9]|$)")),
    ("dummy", re.compile(r"(?i)(?:^|[^a-z0-9])dummy(?:[^a-z0-9]|$)")),
    ("sample", re.compile(r"(?i)(?:^|[^a-z0-9])sample(?:[^a-z0-9]|$)")),
    ("テスト", re.compile("テスト")),
    ("確認用", re.compile("確認用")),
)


def suspicious_reason(value):
    for label, pattern in SUSPICIOUS_PATTERNS:
        if pattern.search(value or ""):
            return label
    return ""


def display_value(value, limit=180):
    normalized = (value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(normalized) > limit:
        normalized = f"{normalized[: limit - 1]}…"
    return json.dumps(normalized, ensure_ascii=False)


@dataclass
class ImportPlan:
    rows: int = 0
    unique_ids: int = 0
    matched: int = 0
    missing_ids: list[int] = field(default_factory=list)
    sku_changes: list[tuple] = field(default_factory=list)
    description_changes: list[tuple] = field(default_factory=list)
    reference_diffs: list[tuple] = field(default_factory=list)
    skipped_products: set[int] = field(default_factory=set)
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    review_products: set[int] = field(default_factory=set)


class Command(BaseCommand):
    help = (
        "Safely import current_sku and current_description from a product data "
        "export. The command is a dry-run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Apply validated SKU and description changes atomically.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report changes without writing (the default).",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"].expanduser()
        rows = self._read_rows(csv_path)

        if options["apply"]:
            with transaction.atomic():
                products = self._load_products(rows, lock=True)
                plan = self._build_plan(rows, products)
                self._write_report(plan, apply=True)
                if plan.errors:
                    raise CommandError(
                        "Import aborted; validation errors were found and no products were changed."
                    )
                try:
                    self._apply(plan, products)
                except IntegrityError as exc:
                    raise CommandError(
                        "Import rolled back because a database constraint was violated; "
                        "no products were changed."
                    ) from exc
        else:
            products = self._load_products(rows, lock=False)
            plan = self._build_plan(rows, products)
            self._write_report(plan, apply=False)
            if plan.errors:
                raise CommandError(
                    "Dry-run found validation errors; fix them before using --apply."
                )

    def _read_rows(self, csv_path):
        if not csv_path.is_file():
            raise CommandError(f"CSV file not found: {csv_path}")
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise CommandError("CSV has no header row.")
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames))
                if missing:
                    raise CommandError(
                        f"CSV is missing required columns: {', '.join(missing)}"
                    )
                return list(reader)
        except UnicodeDecodeError as exc:
            raise CommandError("CSV must be UTF-8 encoded.") from exc
        except csv.Error as exc:
            raise CommandError(f"Invalid CSV: {exc}") from exc

    @staticmethod
    def _parsed_ids(rows):
        parsed = []
        for row_number, row in enumerate(rows, start=2):
            raw_id = (row.get("product_id") or "").strip()
            try:
                product_id = int(raw_id)
                if product_id < 1:
                    raise ValueError
            except ValueError:
                parsed.append((row_number, None, row))
            else:
                parsed.append((row_number, product_id, row))
        return parsed

    def _load_products(self, rows, lock):
        ids = [product_id for _, product_id, _ in self._parsed_ids(rows) if product_id]
        if lock:
            # Do not join the nullable category relation while taking row locks:
            # PostgreSQL rejects FOR UPDATE on the nullable side of an outer join.
            queryset = Product.objects.select_for_update()
        else:
            queryset = Product.objects.select_related("category")
        return queryset.in_bulk(ids)

    def _build_plan(self, rows, products):
        plan = ImportPlan(rows=len(rows))
        parsed = self._parsed_ids(rows)
        valid_ids = [product_id for _, product_id, _ in parsed if product_id]
        counts = Counter(valid_ids)
        plan.unique_ids = len(counts)

        for row_number, product_id, _ in parsed:
            if product_id is None:
                plan.errors.append(f"row {row_number}: invalid product_id")
        for product_id, count in sorted(counts.items()):
            if count > 1:
                plan.errors.append(
                    f"product_id {product_id}: appears {count} times in CSV"
                )

        sku_rows = {}
        for row_number, product_id, row in parsed:
            sku = (row.get("current_sku") or "").strip()
            if sku:
                sku_rows.setdefault(sku.casefold(), []).append(
                    (row_number, product_id, sku)
                )
        for occurrences in sku_rows.values():
            if len(occurrences) > 1:
                locations = ", ".join(
                    f"row {row_number}/product_id {product_id}"
                    for row_number, product_id, _ in occurrences
                )
                plan.errors.append(
                    f"duplicate CSV SKU {occurrences[0][2]!r} "
                    f"(case-insensitive): {locations}"
                )

        plan.matched = sum(product_id in products for product_id in counts)
        plan.missing_ids = sorted(set(counts) - set(products))
        for product_id in plan.missing_ids:
            plan.warnings.append(
                f"product_id {product_id}: not found; no product was created"
            )
            plan.skipped_products.add(product_id)

        for row_number, product_id, row in parsed:
            if product_id is None or counts[product_id] > 1:
                continue
            product = products.get(product_id)
            if product is None:
                continue

            product_has_change = False
            incoming_sku = (row.get("current_sku") or "").strip()
            incoming_description = row.get("current_description") or ""

            existing_review_fields = []
            for field_name in ("sku", "short_description", "description"):
                reason = suspicious_reason(getattr(product, field_name))
                if reason:
                    existing_review_fields.append(f"{field_name} ({reason})")
            if existing_review_fields:
                plan.review_products.add(product_id)
                plan.warnings.append(
                    f"product_id {product_id}: existing DB value requires review: "
                    + ", ".join(existing_review_fields)
                )

            sku_reason = suspicious_reason(incoming_sku)
            if not incoming_sku:
                plan.warnings.append(
                    f"row {row_number}/product_id {product_id}: blank current_sku skipped"
                )
                plan.skipped_products.add(product_id)
            elif incoming_sku.upper().startswith("DRAFT-"):
                plan.warnings.append(
                    f"row {row_number}/product_id {product_id}: placeholder "
                    "current_sku (DRAFT-) skipped"
                )
                plan.skipped_products.add(product_id)
            elif len(incoming_sku) > Product._meta.get_field("sku").max_length:
                plan.errors.append(
                    f"row {row_number}/product_id {product_id}: current_sku exceeds 80 characters"
                )
            elif sku_reason:
                plan.warnings.append(
                    f"row {row_number}/product_id {product_id}: suspicious current_sku "
                    f"({sku_reason}) skipped"
                )
                plan.skipped_products.add(product_id)
                plan.review_products.add(product_id)
            elif incoming_sku != product.sku:
                conflict = (
                    Product.objects.filter(sku__iexact=incoming_sku)
                    .exclude(pk=product_id)
                    .first()
                )
                if conflict:
                    plan.errors.append(
                        f"product_id {product_id}: SKU {incoming_sku!r} is already used "
                        f"by product_id {conflict.pk}"
                    )
                else:
                    plan.sku_changes.append(
                        (product_id, product.name, product.sku, incoming_sku)
                    )
                    product_has_change = True

            description_reason = suspicious_reason(incoming_description)
            if not incoming_description.strip():
                plan.warnings.append(
                    f"row {row_number}/product_id {product_id}: blank current_description skipped"
                )
                plan.skipped_products.add(product_id)
            elif description_reason:
                plan.warnings.append(
                    f"row {row_number}/product_id {product_id}: suspicious "
                    f"current_description ({description_reason}) skipped"
                )
                plan.skipped_products.add(product_id)
                plan.review_products.add(product_id)
            elif incoming_description != product.description:
                plan.description_changes.append(
                    (
                        product_id,
                        product.name,
                        product.description,
                        incoming_description,
                    )
                )
                product_has_change = True

            for csv_field, getter in REFERENCE_FIELDS.items():
                if csv_field not in row:
                    continue
                csv_value = row.get(csv_field) or ""
                db_value = getter(product) or ""
                if csv_value != db_value:
                    plan.reference_diffs.append(
                        (product_id, product.name, csv_field, db_value, csv_value)
                    )

            if not product_has_change and product_id not in plan.skipped_products:
                plan.unchanged += 1

        return plan

    def _write_report(self, plan, apply):
        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(f"Mode: {mode}")
        for product_id, name, before, after in plan.sku_changes:
            self.stdout.write(
                f"CHANGE product_id={product_id} name={display_value(name)} field=sku "
                f"before={display_value(before)} after={display_value(after)}"
            )
        for product_id, name, before, after in plan.description_changes:
            self.stdout.write(
                f"CHANGE product_id={product_id} name={display_value(name)} field=description "
                f"before={display_value(before)} after={display_value(after)}"
            )
        for product_id, name, field_name, before, after in plan.reference_diffs:
            self.stdout.write(
                f"REFERENCE-DIFF product_id={product_id} name={display_value(name)} "
                f"field={field_name} before={display_value(before)} "
                f"after={display_value(after)} action=not-imported"
            )
        for warning in plan.warnings:
            self.stderr.write(self.style.WARNING(f"WARNING {warning}"))
        for error in plan.errors:
            self.stderr.write(self.style.ERROR(f"ERROR {error}"))

        self.stdout.write(
            "Summary: "
            f"csv_rows={plan.rows} unique_product_ids={plan.unique_ids} "
            f"matched={plan.matched} missing={len(plan.missing_ids)} "
            f"sku_changes={len(plan.sku_changes)} "
            f"description_changes={len(plan.description_changes)} "
            f"unchanged={plan.unchanged} skipped={len(plan.skipped_products)} "
            f"review={len(plan.review_products)} warnings={len(plan.warnings)} "
            f"errors={len(plan.errors)}"
        )

    def _apply(self, plan, products):
        changes_by_product = {}
        for product_id, _, _, after in plan.sku_changes:
            changes_by_product.setdefault(product_id, {})["sku"] = after
        for product_id, _, _, after in plan.description_changes:
            changes_by_product.setdefault(product_id, {})["description"] = after

        updated_products = 0
        for product_id, changes in changes_by_product.items():
            product = products[product_id]
            for field_name, value in changes.items():
                setattr(product, field_name, value)
            product.save(update_fields=(*changes.keys(), "updated_at"))
            updated_products += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Applied: updated_products={updated_products} "
                f"updated_fields={sum(len(values) for values in changes_by_product.values())}"
            )
        )
