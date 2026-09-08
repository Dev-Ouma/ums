from django import forms
from django.utils.text import slugify

from .models import (BLOCK_VARIANTS, Block, MediaAsset, MenuItem, Page,
                     SiteSettings)

FIELD = "form-control"
SELECT = "form-select"
CHECK = "form-check-input"


def _style(form):
    """Apply the dashboard's form styling without repeating widget attrs."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", CHECK)
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            widget.attrs.setdefault("class", SELECT)
        else:
            widget.attrs.setdefault("class", FIELD)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class SiteSettingsForm(StyledModelForm):
    class Meta:
        model = SiteSettings
        exclude = ["updated_at"]
        widgets = {
            "footer_blurb": forms.Textarea(attrs={"rows": 3}),
        }


class PageForm(StyledModelForm):
    class Meta:
        model = Page
        fields = ["title", "slug", "layout", "status", "meta_description"]
        widgets = {"meta_description": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "slug": "The page address. Use “home” to take over the site's front page.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False

    def clean_slug(self):
        slug = slugify(self.cleaned_data.get("slug") or self.data.get("title", ""))
        if not slug:
            raise forms.ValidationError("Enter a title or a page address.")
        clash = Page.objects.filter(slug=slug).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another page already uses that address.")
        return slug


class BlockForm(StyledModelForm):
    class Meta:
        model = Block
        fields = ["block_type", "variant", "heading", "subheading", "body", "image",
                  "link_text", "link_url", "secondary_link_text", "secondary_link_url",
                  "item_limit", "is_visible"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
        }
        help_texts = {
            "body": "Basic formatting is allowed (paragraphs, lists, links, bold). "
                    "Scripts, styles and embeds are stripped when you save.",
            "item_limit": "How many items to show — live-data blocks only.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer only the variants that belong to the block's own type.
        block_type = (self.data.get("block_type")
                      or self.initial.get("block_type")
                      or getattr(self.instance, "block_type", ""))
        self.fields["variant"] = forms.ChoiceField(
            choices=BLOCK_VARIANTS.get(block_type, [("default", "Default")]),
            required=False, widget=forms.Select(attrs={"class": SELECT}),
            label="Template variant")


class BlockTypeForm(forms.Form):
    """Step one of adding a block: pick the type, then edit its fields."""
    block_type = forms.ChoiceField(
        choices=Block._meta.get_field("block_type").choices,
        widget=forms.Select(attrs={"class": SELECT}))


class MenuItemForm(StyledModelForm):
    class Meta:
        model = MenuItem
        fields = ["label", "location", "page", "url", "order",
                  "open_in_new_tab", "is_visible"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["page"].queryset = Page.objects.all()
        self.fields["page"].empty_label = "— use the URL below —"

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("page") and not (cleaned.get("url") or "").strip():
            raise forms.ValidationError("Choose a page or enter a URL.")
        return cleaned


class MediaAssetForm(StyledModelForm):
    MAX_BYTES = 5 * 1024 * 1024
    ALLOWED = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}

    class Meta:
        model = MediaAsset
        fields = ["title", "file"]

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if not upload or not hasattr(upload, "content_type"):
            return upload
        if upload.size > self.MAX_BYTES:
            raise forms.ValidationError("Image is too large — please keep it under 5 MB.")
        if upload.content_type not in self.ALLOWED:
            raise forms.ValidationError("Unsupported file type. Use JPEG, PNG, GIF or WebP.")
        return upload
