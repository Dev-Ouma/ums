"""Content models for the public site.

The CMS is deliberately *structured* rather than free-form: an editor picks a
layout for a page and fills it with typed blocks, each of which renders through
a vetted template partial. Editors never author Django template source — see
`cms/sanitizer.py` and the note in BLOCK_TYPES for why.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from .sanitizer import clean_html

# --------------------------------------------------------------------------
# Registries — the whitelist of templates an editor may choose between.
# Adding a layout or block type means adding a template file and an entry
# here; it is never driven by editor-supplied strings.
# --------------------------------------------------------------------------
PAGE_LAYOUTS = [
    ("landing", "Landing page — full-bleed sections, no page header"),
    ("standard", "Standard page — centred column with a page header"),
    ("wide", "Wide page — full-width container, no page header"),
]

BLOCK_TYPES = [
    ("hero", "Hero banner"),
    ("rich_text", "Text section"),
    ("stats", "Statistics strip"),
    ("features", "Feature grid"),
    ("cards", "Portal / link cards"),
    ("cta", "Call to action"),
    ("image", "Image / banner"),
    ("departments", "Departments (live data)"),
    ("courses", "Featured courses (live data)"),
    ("events", "Upcoming events (live data)"),
]

# Per-type template variants. The key is the block type; each entry is the
# list of (value, label) choices offered for that type.
BLOCK_VARIANTS = {
    "hero": [("image", "Background image"), ("gradient", "Solid gradient"), ("split", "Split with image")],
    "rich_text": [("default", "Full width"), ("narrow", "Narrow column"), ("boxed", "Boxed card")],
    "stats": [("cards", "Cards"), ("inline", "Inline strip")],
    "features": [("grid3", "Three columns"), ("grid4", "Four columns"), ("list", "Vertical list")],
    "cards": [("icons", "Icon cards"), ("plain", "Plain cards")],
    "cta": [("gradient", "Gradient panel"), ("outline", "Outlined panel")],
    "image": [("full", "Full width"), ("rounded", "Rounded card")],
    "departments": [("grid", "Grid"), ("list", "List")],
    "courses": [("grid", "Grid"), ("list", "List")],
    "events": [("cards", "Cards"), ("list", "List")],
}

MENU_LOCATIONS = [
    ("header", "Main header"),
    ("topbar", "Top utility bar"),
    ("footer_explore", "Footer — Explore"),
    ("footer_portals", "Footer — Portals"),
]


class SingletonModel(models.Model):
    """A model with exactly one row, addressed as `Model.load()`."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The site settings row cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SiteSettings(SingletonModel):
    """Global branding and contact details shown across the public site."""

    site_name = models.CharField(max_length=120, default="University Management System")
    site_short_name = models.CharField(max_length=30, default="UMS")
    topbar_text = models.CharField(
        max_length=200, blank=True, default="Welcome To University Management System")
    logo = models.ImageField(upload_to="cms/branding/", blank=True, null=True)

    contact_email = models.EmailField(blank=True, default="hello@example.com")
    contact_phone = models.CharField(max_length=40, blank=True, default="0000")
    contact_address = models.CharField(
        max_length=200, blank=True, default="Knowledge City, Campus Road")

    facebook_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)

    footer_blurb = models.TextField(
        blank=True,
        default="A modern, all-in-one platform to run admissions, academics, attendance, "
                "exams, fees and campus life — powered by built-in AI insights.")
    footer_copyright = models.CharField(
        max_length=200, blank=True,
        default="University Management System · Built with Django · Powered by StackGee")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return self.site_name

    @property
    def social_links(self):
        """Only the networks that have actually been filled in."""
        pairs = [
            ("fa-facebook", self.facebook_url),
            ("fa-x-twitter", self.twitter_url),
            ("fa-instagram", self.instagram_url),
            ("fa-youtube", self.youtube_url),
        ]
        return [{"icon": icon, "url": url} for icon, url in pairs if url]


class PageQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Page.PUBLISHED)

    def visible_to(self, user):
        """Drafts are visible to site managers so they can preview their work."""
        if user is not None and user.is_authenticated and (
                user.is_superuser or user.is_admin_role):
            return self
        return self.published()


class Page(models.Model):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    STATUS = [(DRAFT, "Draft"), (PUBLISHED, "Published")]

    # Slugs that the CMS may own but which are also served by hand-written
    # views; the CMS version takes over only once it is published.
    RESERVED_HOME = "home"

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    layout = models.CharField(max_length=20, choices=PAGE_LAYOUTS, default="standard")
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT)

    meta_description = models.CharField(
        max_length=300, blank=True,
        help_text="Shown by search engines under the page title.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="cms_pages")

    objects = PageQuerySet.as_manager()

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:160]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        if self.slug == self.RESERVED_HOME:
            return reverse("university:home")
        # Resolved from the root URLconf, where the catch-all slug route is
        # registered after every hand-written path.
        return reverse("cms_page", args=[self.slug])

    @property
    def is_published(self):
        return self.status == self.PUBLISHED

    @property
    def layout_template(self):
        return f"public/layouts/{self.layout}.html"

    def visible_blocks(self):
        return self.blocks.filter(is_visible=True)


class Block(models.Model):
    """One section of a page.

    `body` holds editor-authored HTML, sanitised on save against a small
    allowlist. Storing the sanitised form means a later change to the
    allowlist cannot retroactively expose markup that was already saved.
    """

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="blocks")
    block_type = models.CharField(max_length=20, choices=BLOCK_TYPES)
    variant = models.CharField(max_length=20, blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    heading = models.CharField(max_length=200, blank=True)
    subheading = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    image = models.ImageField(upload_to="cms/blocks/", blank=True, null=True)

    link_text = models.CharField(max_length=80, blank=True)
    link_url = models.CharField(max_length=300, blank=True)
    secondary_link_text = models.CharField(max_length=80, blank=True)
    secondary_link_url = models.CharField(max_length=300, blank=True)

    # Dynamic blocks (departments/courses/events) use this as their row limit.
    item_limit = models.PositiveSmallIntegerField(default=6)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.get_block_type_display()} — {self.heading or self.page.title}"

    def save(self, *args, **kwargs):
        self.body = clean_html(self.body)
        if not self.variant:
            choices = BLOCK_VARIANTS.get(self.block_type, [])
            self.variant = choices[0][0] if choices else "default"
        super().save(*args, **kwargs)

    @property
    def template_name(self):
        """The vetted partial this block renders through.

        Both halves come from server-side registries, never from raw editor
        input, so this cannot be steered at an arbitrary file.
        """
        valid = {v for v, _ in BLOCK_VARIANTS.get(self.block_type, [])}
        variant = self.variant if self.variant in valid else "default"
        return f"public/blocks/{self.block_type}__{variant}.html"

    @property
    def variant_choices(self):
        return BLOCK_VARIANTS.get(self.block_type, [])


class MenuItem(models.Model):
    """A navigation entry. Points either at a CMS page or at an explicit URL."""

    label = models.CharField(max_length=80)
    location = models.CharField(max_length=20, choices=MENU_LOCATIONS, default="header")
    page = models.ForeignKey(Page, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="menu_items")
    url = models.CharField(max_length=300, blank=True,
                           help_text="Used when no page is selected, e.g. /catalog/")
    order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    open_in_new_tab = models.BooleanField(default=False)

    class Meta:
        ordering = ["location", "order", "id"]

    def __str__(self):
        return f"{self.label} ({self.get_location_display()})"

    def clean(self):
        if not self.page and not self.url:
            raise ValidationError("Choose a page or enter a URL.")

    @property
    def href(self):
        if self.page:
            return self.page.get_absolute_url()
        return self.url


class MediaAsset(models.Model):
    """Uploaded images available to blocks and branding."""

    title = models.CharField(max_length=160, blank=True)
    file = models.ImageField(upload_to="cms/media/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="cms_uploads")

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title or self.file.name
