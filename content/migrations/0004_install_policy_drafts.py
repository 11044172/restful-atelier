from datetime import date

from django.db import migrations


def install_drafts(apps, schema_editor):
    PolicyPage = apps.get_model("content", "PolicyPage")
    from content.policy_drafts import POLICY_DRAFTS
    for index, (slug, (title, body)) in enumerate(POLICY_DRAFTS.items(), 1):
        page, created = PolicyPage.objects.get_or_create(slug=slug, defaults={"title": title})
        if created or not page.body.strip():
            page.title = title
            page.body = body
            page.version = "2026-08-11-draft.1"
            page.effective_date = date(2026, 8, 11)
            page.published = True
            page.legal_reviewed = False
            page.sort_order = index
            page.save()


class Migration(migrations.Migration):
    dependencies = [("content", "0003_policypage_effective_date_policypage_legal_reviewed_and_more")]
    operations = [migrations.RunPython(install_drafts, migrations.RunPython.noop)]
