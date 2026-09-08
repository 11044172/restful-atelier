from django.db import migrations


OLD_VERSION = "2026-08-11-draft.1"
NEW_VERSION = "2026-09-08-draft.2"

FORWARD_REPLACEMENTS = (
    ("正式運用前草案", "正式營運前草案"),
    ("事業者", "本網站經營者"),
    ("監査紀錄", "稽核紀錄"),
    ("過入金", "溢付"),
)
REVERSE_REPLACEMENTS = tuple(
    (localized, original) for original, localized in reversed(FORWARD_REPLACEMENTS)
)


def _update_drafts(apps, replacements, source_version, target_version):
    PolicyPage = apps.get_model("content", "PolicyPage")
    for page in PolicyPage.objects.filter(legal_reviewed=False):
        body = page.body
        for source, target in replacements:
            body = body.replace(source, target)
        changed_fields = []
        if body != page.body:
            page.body = body
            changed_fields.append("body")
        if page.version == source_version:
            page.version = target_version
            changed_fields.append("version")
        if changed_fields:
            page.save(update_fields=changed_fields)


def localize_drafts(apps, schema_editor):
    _update_drafts(apps, FORWARD_REPLACEMENTS, OLD_VERSION, NEW_VERSION)


def restore_drafts(apps, schema_editor):
    _update_drafts(apps, REVERSE_REPLACEMENTS, NEW_VERSION, OLD_VERSION)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0005_alter_policypage_legal_reviewed"),
    ]

    operations = [
        migrations.RunPython(localize_drafts, restore_drafts),
    ]
