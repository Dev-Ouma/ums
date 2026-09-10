from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("university", "0030_goliveissue_golivereadiness"),
    ]

    operations = [
        migrations.AddField(
            model_name="notice",
            name="action_label",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="notice",
            name="action_url",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="notice",
            name="animation_direction",
            field=models.CharField(choices=[("LEFT", "Right to left"), ("RIGHT", "Left to right")], default="LEFT", max_length=8),
        ),
        migrations.AddField(
            model_name="notice",
            name="animation_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notice",
            name="animation_speed",
            field=models.PositiveSmallIntegerField(default=28, help_text="Ticker duration in seconds. Higher is slower."),
        ),
        migrations.AddField(
            model_name="notice",
            name="banner_mode",
            field=models.CharField(choices=[("STATIC", "Static"), ("TICKER", "Ticker / Live News")], default="TICKER", max_length=12),
        ),
        migrations.AddField(
            model_name="notice",
            name="dismissible",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notice",
            name="display_order",
            field=models.PositiveSmallIntegerField(default=100),
        ),
        migrations.AddField(
            model_name="notice",
            name="persistent",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name="notice",
            options={"ordering": ["display_order", "-is_pinned", "-created_at"]},
        ),
    ]
