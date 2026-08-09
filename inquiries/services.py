import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_inquiry_emails(inquiry):
    context = {"inquiry": inquiry}
    subject = f"[Rfull] {inquiry.category.display_name} / {inquiry.name}"
    try:
        send_mail(subject, render_to_string("inquiries/email/staff_notification.txt", context), settings.DEFAULT_FROM_EMAIL, [inquiry.category.recipient_email], fail_silently=False)
    except Exception:
        logger.exception("Inquiry staff email failed for %s", inquiry.pk)
    try:
        send_mail("[Rfull] 已收到您的訊息", render_to_string("inquiries/email/customer_confirmation.txt", context), settings.DEFAULT_FROM_EMAIL, [inquiry.email], fail_silently=False)
    except Exception:
        logger.exception("Inquiry customer email failed for %s", inquiry.pk)
