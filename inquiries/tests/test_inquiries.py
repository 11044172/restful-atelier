from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from inquiries.forms import InquiryForm
from inquiries.models import Inquiry, InquiryCategory


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", CONTACT_RATE_LIMIT=50)
class InquiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = InquiryCategory.objects.create(display_name="設計諮詢", slug="design", recipient_email="design@example.com")
        self.valid = {"category": self.category.pk, "name": "林小姐", "phone": "0912", "email": "user@example.com", "budget_range": "未定", "project_location": "台中", "expected_timing": "来年", "message": "相談内容", "privacy_agreed": "on", "newsletter_opt_in": "on", "website": ""}

    def test_required_fields_and_privacy_consent(self):
        form = InquiryForm({"category": self.category.pk, "name": "", "phone": "", "email": "bad", "message": "", "privacy_agreed": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("privacy_agreed", form.errors)

    def test_honeypot_is_rejected(self):
        form = InquiryForm({**self.valid, "website": "spam"})
        self.assertFalse(form.is_valid())

    def test_inquiry_saved_and_routed_to_one_recipient(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("inquiries:contact"), self.valid)
        self.assertEqual(response.status_code, 302)
        inquiry = Inquiry.objects.get()
        self.assertEqual(inquiry.category, self.category)
        self.assertTrue(inquiry.privacy_agreed)
        self.assertTrue(inquiry.newsletter_opt_in)
        self.assertEqual(mail.outbox[0].to, ["design@example.com"])
        self.assertEqual(mail.outbox[1].to, ["user@example.com"])
