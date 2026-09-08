"""Backfill login activity for accounts that pre-date the feature.

Without this, every existing user's account menu would read "—" for first and
last access until they happened to sign in again. date_joined and last_login
are already on record, so use them rather than showing nothing.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(first_seen_at__isnull=True):
        user.first_seen_at = user.date_joined
        if user.last_seen_at is None:
            user.last_seen_at = user.last_login or user.date_joined
        user.save(update_fields=["first_seen_at", "last_seen_at"])


def unbackfill(apps, schema_editor):
    # Reversible, but deliberately a no-op: the backfilled values are
    # indistinguishable from genuinely recorded ones, so clearing them would
    # throw away real activity captured since the migration ran.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_avatar_image_user_first_seen_at_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
