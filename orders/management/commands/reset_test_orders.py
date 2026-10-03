from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from catalog.models import Product
from orders.models import (
    Invoice,
    LineNotification,
    NotificationOutbox,
    Order,
    OrderAuditLog,
    OrderInvoiceProfile,
    OrderItem,
    Payment,
    PolicyAcceptance,
)

CONFIRMATION_PHRASE = "RESET-TEST-ORDERS"

# This explicit schema allow-list makes the one-off command fail closed if a
# future order relation is added without also adding it to the deletion plan.
EXPECTED_ORDER_RELATIONS = {
    "orders.OrderItem": "CASCADE",
    "orders.LineNotification": "CASCADE",
    "orders.NotificationOutbox": "CASCADE",
    "orders.Payment": "PROTECT",
    "orders.OrderInvoiceProfile": "PROTECT",
    "orders.Invoice": "PROTECT",
    "orders.PolicyAcceptance": "PROTECT",
    "orders.OrderAuditLog": "PROTECT",
}


class Command(BaseCommand):
    help = (
        "正式運用開始前のテスト注文を、予約済み在庫の復元後に削除します。"
        "既定は読み取り専用のdry-runです。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="在庫復元と注文関連データ削除を実行する",
        )
        parser.add_argument(
            "--confirm",
            default="",
            metavar=CONFIRMATION_PHRASE,
            help=f"本実行時に必要な確認文字列: {CONFIRMATION_PHRASE}",
        )
        parser.add_argument(
            "--backup-confirmed",
            action="store_true",
            help="現在のDBバックアップと復元手順の確認済みを明示する",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        if execute:
            if options["confirm"] != CONFIRMATION_PHRASE:
                raise CommandError(
                    f"実行を拒否しました。--confirm {CONFIRMATION_PHRASE} が必要です。"
                )
            if not options["backup_confirmed"]:
                raise CommandError(
                    "Have you confirmed that a current database backup exists? "
                    "確認済みの場合だけ --backup-confirmed を付けてください。"
                )

        if not execute:
            plan = self._build_plan(lock=False)
            self._print_plan(plan)
            self._print_safety_issues(plan["errors"])
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("DRY RUN ONLY"))
            self.stdout.write("No database changes have been made.")
            return

        with transaction.atomic():
            plan = self._build_plan(lock=True)
            self._print_plan(plan)
            if plan["errors"]:
                self._print_safety_issues(plan["errors"])
                raise CommandError("安全チェックに失敗したため、全処理を中止しました。")

            product_ids = sorted(plan["inventory"])
            locked_products = {
                product.pk: product
                for product in Product.objects.select_for_update()
                .filter(pk__in=product_ids)
                .order_by("pk")
            }
            if set(locked_products) != set(product_ids):
                raise CommandError(
                    "商品ロック中に対象商品が変更されたため中止しました。"
                )

            # Rebuild after all Order, OrderItem, and Product rows are locked so
            # the exact values validated are the values that will be changed.
            if Order.objects.exclude(pk__in=plan["order_ids"]).exists():
                raise CommandError(
                    "ロック開始後に新しい注文が作成されたため中止しました。"
                )
            plan = self._build_plan(
                lock=True,
                locked_products=locked_products,
                expected_order_ids=plan["order_ids"],
            )
            if plan["errors"]:
                self._print_safety_issues(plan["errors"])
                raise CommandError("ロック後の安全チェックに失敗したため中止しました。")

            for product_id, quantity in sorted(plan["inventory"].items()):
                Product.objects.filter(pk=product_id).update(
                    stock=F("stock") + quantity
                )

            self._delete_related_data(plan["order_ids"])
            self._verify_reset(plan)

            final_stocks = dict(
                Product.objects.filter(pk__in=product_ids).values_list("pk", "stock")
            )
            self.stdout.write("")
            self.stdout.write("Post-reset verification:")
            self._print_zero_counts()
            for row in plan["inventory_rows"]:
                self.stdout.write(
                    f"Product {row['id']} ({row['name']}) stock: {final_stocks[row['id']]}"
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Reset completed successfully."))
        self.stdout.write("No external notifications were sent.")

    def _build_plan(self, *, lock, locked_products=None, expected_order_ids=None):
        orders = Order.objects.order_by("pk")
        items = OrderItem.objects.order_by("pk")
        if expected_order_ids is not None:
            orders = orders.filter(pk__in=expected_order_ids)
        if lock:
            orders = orders.select_for_update()
            items = items.select_for_update()
        orders = list(orders)
        order_ids = [order.pk for order in orders]
        items = list(items.filter(order_id__in=order_ids))
        if lock:
            self._lock_related_rows(order_ids)
        order_by_id = {order.pk: order for order in orders}

        inventory = Counter()
        for item in items:
            order = order_by_id[item.order_id]
            if (
                order.inventory_reserved
                and not order.inventory_released
                and item.product_id
                and item.stock_was_reserved
            ):
                inventory[item.product_id] += item.quantity

        product_ids = sorted(inventory)
        if locked_products is None:
            products = {
                product.pk: product
                for product in Product.objects.filter(pk__in=product_ids).order_by("pk")
            }
        else:
            products = locked_products

        counts = {
            "Orders": len(orders),
            "Order items": len(items),
            "Payments": Payment.objects.filter(order_id__in=order_ids).count(),
            "Invoices": Invoice.objects.filter(order_id__in=order_ids).count(),
            "Invoice profiles": OrderInvoiceProfile.objects.filter(
                order_id__in=order_ids
            ).count(),
            "LINE notifications": LineNotification.objects.filter(
                order_id__in=order_ids
            ).count(),
            "Notification outbox": NotificationOutbox.objects.filter(
                order_id__in=order_ids
            ).count(),
            "Audit logs": OrderAuditLog.objects.filter(order_id__in=order_ids).count(),
            "Policy acceptances": PolicyAcceptance.objects.filter(
                order_id__in=order_ids
            ).count(),
        }
        errors = self._safety_errors(orders, items, order_ids, products)
        rows = []
        for product_id, quantity in sorted(inventory.items()):
            product = products.get(product_id)
            if product is None:
                continue
            rows.append(
                {
                    "id": product_id,
                    "name": product.name,
                    "sku": product.sku,
                    "current": product.stock,
                    "restore": quantity,
                    "after": product.stock + quantity,
                }
            )
        return {
            "orders": orders,
            "order_ids": order_ids,
            "items": items,
            "inventory": dict(inventory),
            "inventory_rows": rows,
            "counts": counts,
            "errors": errors,
            "already_released": sum(order.inventory_released for order in orders),
            "not_reserved": sum(not order.inventory_reserved for order in orders),
        }

    def _safety_errors(self, orders, items, order_ids, products):
        errors = []
        order_by_id = {order.pk: order for order in orders}
        item_count_by_order = Counter(item.order_id for item in items)
        empty_orders = [
            order.pk for order in orders if not item_count_by_order[order.pk]
        ]
        if empty_orders:
            errors.append(f"注文明細がないOrder: {self._ids(empty_orders)}")

        null_products = [item.pk for item in items if item.product_id is None]
        if null_products:
            errors.append(f"product_idがNULLのOrderItem: {self._ids(null_products)}")

        invalid_quantities = [item.pk for item in items if item.quantity <= 0]
        if invalid_quantities:
            errors.append(
                f"quantityが0以下のOrderItem: {self._ids(invalid_quantities)}"
            )

        inconsistent_items = [
            item.pk
            for item in items
            if item.stock_was_reserved
            and not order_by_id[item.order_id].inventory_reserved
        ]
        if inconsistent_items:
            errors.append(
                "inventory_reserved=Falseなのにstock_was_reserved=TrueのOrderItem: "
                f"{self._ids(inconsistent_items)}"
            )
        inconsistent_orders = [
            order.pk
            for order in orders
            if order.inventory_released and not order.inventory_reserved
        ]
        if inconsistent_orders:
            errors.append(
                "inventory_reserved=Falseなのにinventory_released=TrueのOrder: "
                f"{self._ids(inconsistent_orders)}"
            )

        required_products = {
            item.product_id
            for item in items
            if item.product_id
            and item.stock_was_reserved
            and order_by_id[item.order_id].inventory_reserved
            and not order_by_id[item.order_id].inventory_released
        }
        missing_products = required_products - set(products)
        if missing_products:
            errors.append(
                f"復元対象のProductが存在しません: {self._ids(missing_products)}"
            )

        relation_map = {
            relation.related_model._meta.label: getattr(
                relation.field.remote_field.on_delete, "__name__", "unknown"
            )
            for relation in Order._meta.related_objects
        }
        for label, on_delete in sorted(relation_map.items()):
            expected = EXPECTED_ORDER_RELATIONS.get(label)
            if expected is None:
                relation = next(
                    rel
                    for rel in Order._meta.related_objects
                    if rel.related_model._meta.label == label
                )
                count = relation.related_model._default_manager.filter(
                    **{f"{relation.field.name}_id__in": order_ids}
                ).count()
                errors.append(f"未対応の注文関連モデル {label}: {count}件")
            elif expected != on_delete:
                errors.append(
                    f"{label}の削除制約が変更されています: "
                    f"想定={expected}, 実際={on_delete}"
                )
        missing_relations = set(EXPECTED_ORDER_RELATIONS) - set(relation_map)
        if missing_relations:
            errors.append(
                f"想定した注文関連が存在しません: {', '.join(sorted(missing_relations))}"
            )

        dangerous_payment_ids = []
        for payment in Payment.objects.filter(order_id__in=order_ids, provider="ecpay"):
            metadata = payment.provider_metadata or {}
            callback = metadata.get("callback") or {}
            callback_merchant = str(callback.get("MerchantID") or "")
            current_merchant = str(getattr(settings, "ECPAY_MERCHANT_ID", "") or "")
            same_or_unknown_merchant = (
                not callback_merchant
                or not current_merchant
                or callback_merchant == current_merchant
            )
            real_callback = str(callback.get("SimulatePaid", "0")) != "1"
            has_external_result = bool(
                payment.merchant_trade_no
                or payment.provider_reference
                or payment.provider_event_id
                or payment.status == Payment.Status.CONFIRMED
            )
            if (
                getattr(settings, "ECPAY_ENV", "stage") == "production"
                and same_or_unknown_merchant
                and real_callback
                and has_external_result
            ):
                dangerous_payment_ids.append(payment.pk)
        if dangerous_payment_ids:
            errors.append(
                "本番ECPayの実取引の可能性があるPayment: "
                f"{self._ids(dangerous_payment_ids)}"
            )

        dangerous_invoice_ids = []
        invoices = Invoice.objects.filter(order_id__in=order_ids).select_related(
            "profile"
        )
        for invoice in invoices:
            production_snapshot = (invoice.profile.configuration_snapshot or {}).get(
                "environment"
            ) == "production"
            external_activity = bool(
                invoice.attempt_count
                or invoice.invoice_no
                or invoice.issued_at
                or invoice.voided_at
                or invoice.status != Invoice.Status.PENDING
            )
            if production_snapshot and external_activity:
                dangerous_invoice_ids.append(invoice.pk)
        if dangerous_invoice_ids:
            errors.append(
                "本番ECPay Invoiceへの送信・発行の可能性があるInvoice: "
                f"{self._ids(dangerous_invoice_ids)}"
            )
        processing_outbox = list(
            NotificationOutbox.objects.filter(
                order_id__in=order_ids,
                status=NotificationOutbox.Status.PROCESSING,
            ).values_list("pk", flat=True)
        )
        if processing_outbox:
            errors.append(
                "通知workerが処理中のNotificationOutbox: "
                f"{self._ids(processing_outbox)}"
            )
        return errors

    def _lock_related_rows(self, order_ids):
        # Force evaluation. In particular, locking outbox rows makes the normal
        # worker's skip_locked query leave them alone during this transaction.
        models = (
            OrderInvoiceProfile,
            Invoice,
            Payment,
            LineNotification,
            NotificationOutbox,
            PolicyAcceptance,
            OrderAuditLog,
        )
        for model in models:
            list(
                model.objects.select_for_update()
                .filter(order_id__in=order_ids)
                .order_by("pk")
                .values_list("pk", flat=True)
            )

    def _delete_related_data(self, order_ids):
        # No model save(), business operation, notification enqueue, or external
        # provider function is called here.
        Invoice.objects.filter(order_id__in=order_ids).delete()
        Payment.objects.filter(order_id__in=order_ids).delete()
        OrderInvoiceProfile.objects.filter(order_id__in=order_ids).delete()
        LineNotification.objects.filter(order_id__in=order_ids).delete()
        NotificationOutbox.objects.filter(order_id__in=order_ids).delete()
        PolicyAcceptance.objects.filter(order_id__in=order_ids).delete()
        OrderAuditLog.objects.filter(order_id__in=order_ids).delete()
        OrderItem.objects.filter(order_id__in=order_ids).delete()
        Order.objects.filter(pk__in=order_ids).delete()

    def _verify_reset(self, plan):
        remaining = {
            "Orders": Order.objects.count(),
            "OrderItems": OrderItem.objects.count(),
            "Payments linked to deleted orders": Payment.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Invoices linked to deleted orders": Invoice.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Invoice profiles linked to deleted orders": OrderInvoiceProfile.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Notifications linked to deleted orders": LineNotification.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Outbox jobs linked to deleted orders": NotificationOutbox.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Audit logs linked to deleted orders": OrderAuditLog.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
            "Policy acceptances linked to deleted orders": PolicyAcceptance.objects.filter(
                order_id__in=plan["order_ids"]
            ).count(),
        }
        nonzero = {label: count for label, count in remaining.items() if count}
        if nonzero:
            details = ", ".join(f"{label}={count}" for label, count in nonzero.items())
            raise CommandError(f"実行後検証に失敗しました: {details}")

        actual_stocks = dict(
            Product.objects.filter(pk__in=plan["inventory"]).values_list("pk", "stock")
        )
        for row in plan["inventory_rows"]:
            if actual_stocks.get(row["id"]) != row["after"]:
                raise CommandError(
                    f"Product {row['id']}の在庫検証に失敗しました: "
                    f"想定={row['after']}, 実際={actual_stocks.get(row['id'])}"
                )

    def _print_plan(self, plan):
        self.stdout.write(f"All orders: {plan['counts']['Orders']}")
        self.stdout.write(f"Orders to delete: {plan['counts']['Orders']}")
        for label in (
            "Order items",
            "Payments",
            "Invoices",
            "Invoice profiles",
            "LINE notifications",
            "Notification outbox",
            "Audit logs",
            "Policy acceptances",
        ):
            self.stdout.write(f"{label}: {plan['counts'][label]}")
        other = sum(
            plan["counts"][label]
            for label in (
                "LINE notifications",
                "Notification outbox",
                "Audit logs",
                "Policy acceptances",
            )
        )
        self.stdout.write(f"Other order-related records: {other}")
        self.stdout.write("")
        self.stdout.write("Inventory restoration:")
        self.stdout.write("ID  Product  SKU  Current  Restore  After")
        for row in plan["inventory_rows"]:
            self.stdout.write(
                f"{row['id']}  {row['name']}  {row['sku']}  "
                f"{row['current']}  +{row['restore']}  {row['after']}"
            )
        if not plan["inventory_rows"]:
            self.stdout.write("(none)")
        self.stdout.write("")
        self.stdout.write(
            f"Orders with already released inventory: {plan['already_released']}"
        )
        self.stdout.write(
            f"Orders without inventory reservation: {plan['not_reserved']}"
        )

    def _print_safety_issues(self, errors):
        if not errors:
            self.stdout.write(self.style.SUCCESS("Safety checks: OK"))
            return
        self.stdout.write(self.style.ERROR("Safety checks: FAILED"))
        for error in errors:
            self.stdout.write(f"- {error}")
        self.stdout.write("--execute would stop without changing the database.")

    def _print_zero_counts(self):
        self.stdout.write(f"Orders: {Order.objects.count()}")
        self.stdout.write(f"OrderItems: {OrderItem.objects.count()}")
        self.stdout.write(
            f"Payments linked to deleted orders: {Payment.objects.count()}"
        )
        self.stdout.write(
            f"Invoices linked to deleted orders: {Invoice.objects.count()}"
        )
        self.stdout.write(
            f"Invoice profiles linked to deleted orders: {OrderInvoiceProfile.objects.count()}"
        )
        self.stdout.write(
            f"Notifications linked to deleted orders: {LineNotification.objects.count()}"
        )
        self.stdout.write(
            f"Outbox jobs linked to deleted orders: {NotificationOutbox.objects.count()}"
        )
        self.stdout.write(
            f"Audit logs linked to deleted orders: {OrderAuditLog.objects.count()}"
        )
        self.stdout.write(
            f"Policy acceptances linked to deleted orders: {PolicyAcceptance.objects.count()}"
        )

    @staticmethod
    def _ids(values):
        values = sorted(values)
        preview = ", ".join(str(value) for value in values[:20])
        if len(values) > 20:
            preview += f", ... ({len(values)} total)"
        return preview
