"""Tests for the site CMS: permissions, editing, rendering and sanitising."""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import Role, User
from cms.models import Block, MediaAsset, MenuItem, Page, SiteSettings
from cms.sanitizer import clean_html
from university.models import Department

MEDIA = tempfile.mkdtemp(prefix="ums-cms-test-")
PASSWORD = "CmsTestPass123"


def png(name="pic.png"):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (40, 80, 160)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class CmsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="cms.admin", password=PASSWORD, role=Role.ADMIN,
            first_name="Cms", last_name="Admin", email="cms.admin@ums.test")
        cls.faculty = User.objects.create_user(
            username="cms.faculty", password=PASSWORD, role=Role.FACULTY,
            email="cms.faculty@ums.test")
        cls.student = User.objects.create_user(
            username="cms.student", password=PASSWORD, role=Role.STUDENT,
            email="cms.student@ums.test")
        cls.page = Page.objects.create(title="About Us", slug="about-us",
                                       layout="standard", status=Page.PUBLISHED)
        Block.objects.create(page=cls.page, block_type="rich_text", variant="narrow",
                             heading="Our story", body="<p>Founded in 1975.</p>", order=0)

    def login(self, user):
        client = Client()
        self.assertTrue(client.login(username=user.username, password=PASSWORD))
        return client


class PermissionTests(CmsTestBase):
    """Only administrators may reach the manager UI."""

    MANAGER_URLS = [
        ("cms:dashboard", []), ("cms:settings", []), ("cms:page_list", []),
        ("cms:page_create", []), ("cms:menu_list", []), ("cms:menu_create", []),
        ("cms:media", []),
    ]

    def test_anonymous_is_sent_to_login(self):
        client = Client()
        for name, args in self.MANAGER_URLS:
            with self.subTest(view=name):
                response = client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.url)

    def test_non_admin_roles_are_refused(self):
        for user in (self.faculty, self.student):
            client = self.login(user)
            for name, args in self.MANAGER_URLS:
                with self.subTest(role=user.role, view=name):
                    self.assertEqual(client.get(reverse(name, args=args)).status_code, 403)

    def test_admin_reaches_every_manager_view(self):
        client = self.login(self.admin)
        for name, args in self.MANAGER_URLS:
            with self.subTest(view=name):
                self.assertEqual(client.get(reverse(name, args=args)).status_code, 200)

    def test_superuser_without_admin_role_is_allowed(self):
        root = User.objects.create_superuser(
            username="root", password=PASSWORD, email="root@ums.test")
        root.role = Role.STUDENT
        root.save(update_fields=["role"])
        client = self.login(root)
        self.assertEqual(client.get(reverse("cms:dashboard")).status_code, 200)

    def test_write_endpoints_reject_non_admins(self):
        client = self.login(self.student)
        response = client.post(reverse("cms:page_delete", args=[self.page.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Page.objects.filter(pk=self.page.pk).exists())

    def test_destructive_endpoints_reject_get(self):
        client = self.login(self.admin)
        for name in ("cms:page_delete", "cms:page_status"):
            with self.subTest(view=name):
                self.assertEqual(client.get(reverse(name, args=[self.page.pk])).status_code, 405)


class PageEditingTests(CmsTestBase):
    def test_admin_creates_a_page_and_slug_is_derived(self):
        client = self.login(self.admin)
        response = client.post(reverse("cms:page_create"), {
            "title": "Campus Life", "slug": "", "layout": "landing",
            "status": Page.DRAFT, "meta_description": "Life on campus",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        created = Page.objects.get(title="Campus Life")
        self.assertEqual(created.slug, "campus-life")
        self.assertEqual(created.updated_by, self.admin)

    def test_duplicate_slug_is_rejected(self):
        client = self.login(self.admin)
        response = client.post(reverse("cms:page_create"), {
            "title": "Another", "slug": "about-us", "layout": "standard",
            "status": Page.DRAFT, "meta_description": "",
        })
        self.assertContains(response, "already uses that address")
        self.assertEqual(Page.objects.filter(slug="about-us").count(), 1)

    def test_publish_toggle_flips_status_both_ways(self):
        client = self.login(self.admin)
        url = reverse("cms:page_status", args=[self.page.pk])
        client.post(url, follow=True)
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.DRAFT)
        client.post(url, follow=True)
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.PUBLISHED)

    def test_deleting_a_page_removes_its_blocks(self):
        client = self.login(self.admin)
        client.post(reverse("cms:page_delete", args=[self.page.pk]), follow=True)
        self.assertFalse(Page.objects.filter(pk=self.page.pk).exists())
        self.assertFalse(Block.objects.filter(page_id=self.page.pk).exists())

    def test_layout_choice_selects_the_template(self):
        for layout in ("landing", "standard", "wide"):
            with self.subTest(layout=layout):
                self.page.layout = layout
                self.assertEqual(self.page.layout_template, f"public/layouts/{layout}.html")


class BlockEditingTests(CmsTestBase):
    def test_admin_adds_a_block_and_it_lands_last(self):
        client = self.login(self.admin)
        client.post(reverse("cms:block_create", args=[self.page.pk]), {
            "block_type": "cta", "variant": "gradient", "heading": "Join us",
            "subheading": "", "body": "", "link_text": "Apply", "link_url": "/apply/",
            "secondary_link_text": "", "secondary_link_url": "", "item_limit": 6,
            "is_visible": "on",
        }, follow=True)
        blocks = list(self.page.blocks.all())
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[-1].heading, "Join us")

    def test_moving_a_block_reorders_the_page(self):
        client = self.login(self.admin)
        second = Block.objects.create(page=self.page, block_type="cta",
                                      heading="Second", order=1)
        client.post(reverse("cms:block_move", args=[second.pk, "up"]), follow=True)
        self.assertEqual([b.heading for b in self.page.blocks.all()],
                         ["Second", "Our story"])

    def test_moving_past_the_end_is_a_no_op(self):
        client = self.login(self.admin)
        only = self.page.blocks.first()
        client.post(reverse("cms:block_move", args=[only.pk, "down"]), follow=True)
        self.assertEqual(self.page.blocks.count(), 1)

    def test_variant_defaults_to_the_first_for_its_type(self):
        block = Block.objects.create(page=self.page, block_type="features")
        self.assertEqual(block.variant, "grid3")

    def test_template_name_falls_back_when_variant_is_invalid(self):
        block = Block.objects.create(page=self.page, block_type="cta", variant="gradient")
        Block.objects.filter(pk=block.pk).update(variant="../../etc/passwd")
        block.refresh_from_db()
        self.assertEqual(block.template_name, "public/blocks/cta__default.html")

    def test_hidden_blocks_are_left_out_of_rendering(self):
        Block.objects.create(page=self.page, block_type="cta", heading="Hidden one",
                             is_visible=False, order=1)
        self.assertEqual([b.heading for b in self.page.visible_blocks()], ["Our story"])


class SanitiserTests(TestCase):
    def test_scripts_and_their_contents_are_removed(self):
        self.assertEqual(clean_html("<script>alert(1)</script>ok"), "ok")

    def test_style_and_iframe_contents_are_removed(self):
        self.assertEqual(clean_html("<style>b{}</style>hi"), "hi")
        self.assertEqual(clean_html("<iframe src='http://x'>y</iframe>z"), "z")

    def test_event_handlers_are_dropped(self):
        self.assertEqual(clean_html('<p onclick="evil()">hi</p>'), "<p>hi</p>")

    def test_javascript_urls_are_dropped(self):
        self.assertEqual(clean_html('<a href="javascript:alert(1)">x</a>'), "<a>x</a>")

    def test_data_urls_are_dropped(self):
        self.assertNotIn("data:", clean_html('<a href="data:text/html,<script>">x</a>'))

    def test_safe_markup_survives(self):
        html = "<p>Hello <strong>world</strong></p><ul><li>one</li></ul>"
        self.assertEqual(clean_html(html), html)

    def test_relative_links_survive(self):
        self.assertEqual(clean_html('<a href="/catalog/">c</a>'), '<a href="/catalog/">c</a>')

    def test_new_tab_links_get_noopener(self):
        self.assertIn('rel="noopener noreferrer"',
                      clean_html('<a href="https://x.test" target="_blank">x</a>'))

    def test_unbalanced_markup_is_closed(self):
        self.assertEqual(clean_html("<p>dangling"), "<p>dangling</p>")

    def test_block_body_is_sanitised_on_save(self):
        page = Page.objects.create(title="X", slug="x")
        block = Block.objects.create(page=page, block_type="rich_text",
                                     body="<script>alert(1)</script><p>fine</p>")
        block.refresh_from_db()
        self.assertNotIn("script", block.body)
        self.assertIn("<p>fine</p>", block.body)


class PublicRenderingTests(CmsTestBase):
    def test_published_page_is_public(self):
        response = Client().get(self.page.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Our story")
        self.assertContains(response, "Founded in 1975")

    def test_draft_is_hidden_from_visitors(self):
        self.page.status = Page.DRAFT
        self.page.save(update_fields=["status"])
        self.assertEqual(Client().get(self.page.get_absolute_url()).status_code, 404)

    def test_draft_is_previewable_by_an_admin(self):
        self.page.status = Page.DRAFT
        self.page.save(update_fields=["status"])
        client = self.login(self.admin)
        response = client.get(self.page.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft preview")

    def test_draft_is_not_previewable_by_a_student(self):
        self.page.status = Page.DRAFT
        self.page.save(update_fields=["status"])
        client = self.login(self.student)
        self.assertEqual(client.get(self.page.get_absolute_url()).status_code, 404)

    def test_unknown_slug_is_a_404(self):
        self.assertEqual(Client().get("/no-such-page/").status_code, 404)

    def test_hand_written_routes_win_over_a_colliding_slug(self):
        """A CMS page named "about" must not shadow the existing /about/ view."""
        Page.objects.create(title="Hijack", slug="about", status=Page.PUBLISHED)
        response = Client().get("/about/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Hijack")

    def test_publishing_a_home_page_takes_over_the_front_page(self):
        home = Page.objects.create(title="Welcome", slug=Page.RESERVED_HOME,
                                   layout="landing", status=Page.PUBLISHED)
        Block.objects.create(page=home, block_type="cta", variant="gradient",
                             heading="Brand new front page")
        response = Client().get(reverse("university:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brand new front page")

    def test_front_page_falls_back_when_no_cms_home_exists(self):
        self.assertFalse(Page.objects.filter(slug=Page.RESERVED_HOME).exists())
        response = Client().get(reverse("university:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "University")

    def test_a_draft_home_page_does_not_take_over_for_visitors(self):
        home = Page.objects.create(title="Nope", slug=Page.RESERVED_HOME,
                                   layout="landing", status=Page.DRAFT)
        Block.objects.create(page=home, block_type="cta", heading="Unfinished")
        response = Client().get(reverse("university:home"))
        self.assertNotContains(response, "Unfinished")

    def test_every_block_type_and_variant_renders(self):
        """Guards the registry against an entry with no working template."""
        from cms.models import BLOCK_TYPES, BLOCK_VARIANTS
        Department.objects.create(name="Computer Science", code="CSE")
        for block_type, _ in BLOCK_TYPES:
            for variant, _label in BLOCK_VARIANTS[block_type]:
                with self.subTest(block=block_type, variant=variant):
                    page = Page.objects.create(
                        title=f"{block_type}-{variant}", slug=f"{block_type}-{variant}",
                        layout="landing", status=Page.PUBLISHED)
                    Block.objects.create(
                        page=page, block_type=block_type, variant=variant,
                        heading="Heading", subheading="Sub",
                        body="A | B | fa-star | /x/\nC | D | fa-star | /y/")
                    response = Client().get(page.get_absolute_url())
                    self.assertEqual(response.status_code, 200)


class SiteSettingsTests(CmsTestBase):
    def test_settings_are_a_singleton(self):
        first = SiteSettings.load()
        second = SiteSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SiteSettings.objects.count(), 1)

    def test_admin_can_edit_branding_and_it_shows_on_the_site(self):
        client = self.login(self.admin)
        obj = SiteSettings.load()
        data = {f.name: getattr(obj, f.name) or ""
                for f in SiteSettings._meta.fields if f.name not in ("id", "updated_at", "logo")}
        data["site_short_name"] = "ACME"
        data["contact_email"] = "hello@acme.test"
        client.post(reverse("cms:settings"), data, follow=True)
        obj.refresh_from_db()
        self.assertEqual(obj.site_short_name, "ACME")
        html = Client().get(reverse("university:home")).content.decode()
        self.assertIn("ACME", html)
        self.assertIn("hello@acme.test", html)

    def test_social_links_only_list_the_ones_that_are_set(self):
        obj = SiteSettings.load()
        obj.facebook_url = "https://facebook.test/x"
        obj.save()
        self.assertEqual([l["icon"] for l in obj.social_links], ["fa-facebook"])


class MenuTests(CmsTestBase):
    def test_header_menu_replaces_the_built_in_navigation(self):
        MenuItem.objects.create(label="Admissions", url="/admissions/", location="header")
        html = Client().get(reverse("university:home")).content.decode()
        self.assertIn("Admissions", html)

    def test_navigation_falls_back_when_no_menu_is_configured(self):
        self.assertFalse(MenuItem.objects.filter(location="header").exists())
        html = Client().get(reverse("university:home")).content.decode()
        self.assertIn("About Us", html)

    def test_hidden_items_are_not_rendered(self):
        MenuItem.objects.create(label="Secret", url="/secret/", location="header",
                                is_visible=False)
        html = Client().get(reverse("university:home")).content.decode()
        self.assertNotIn("Secret", html)

    def test_a_menu_item_pointing_at_a_page_uses_that_page_url(self):
        item = MenuItem.objects.create(label="About", page=self.page, location="header")
        self.assertEqual(item.href, self.page.get_absolute_url())

    def test_menu_item_needs_a_page_or_a_url(self):
        client = self.login(self.admin)
        response = client.post(reverse("cms:menu_create"), {
            "label": "Nowhere", "location": "header", "page": "", "url": "",
            "order": 0, "is_visible": "on",
        })
        self.assertContains(response, "Choose a page or enter a URL")
        self.assertFalse(MenuItem.objects.filter(label="Nowhere").exists())


@override_settings(MEDIA_ROOT=MEDIA)
class MediaTests(CmsTestBase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_admin_uploads_an_image(self):
        client = self.login(self.admin)
        response = client.post(reverse("cms:media"),
                               {"title": "Campus", "file": png()}, follow=True)
        self.assertEqual(response.status_code, 200)
        asset = MediaAsset.objects.get(title="Campus")
        self.assertEqual(asset.uploaded_by, self.admin)

    def test_non_image_payload_is_rejected(self):
        client = self.login(self.admin)
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        response = client.post(reverse("cms:media"), {"title": "Bad", "file": bad}, follow=True)
        self.assertContains(response, "Upload a valid image")
        self.assertFalse(MediaAsset.objects.filter(title="Bad").exists())

    def test_admin_deletes_an_image(self):
        client = self.login(self.admin)
        asset = MediaAsset.objects.create(title="Old", file=png("old.png"))
        client.post(reverse("cms:media_delete", args=[asset.pk]), follow=True)
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())


class SeedCommandTests(TestCase):
    def test_seed_is_idempotent_and_defaults_to_draft(self):
        from django.core.management import call_command
        call_command("seed_cms", verbosity=0)
        call_command("seed_cms", verbosity=0)
        self.assertEqual(Page.objects.filter(slug=Page.RESERVED_HOME).count(), 1)
        home = Page.objects.get(slug=Page.RESERVED_HOME)
        self.assertEqual(home.status, Page.DRAFT)
        self.assertEqual(home.blocks.count(), 7)
        # The hand-written front page is still what visitors get.
        self.assertNotContains(Client().get(reverse("university:home")), "Choose your portal")

    def test_seed_with_publish_takes_over_the_front_page(self):
        from django.core.management import call_command
        call_command("seed_cms", "--publish", verbosity=0)
        self.assertContains(Client().get(reverse("university:home")), "Choose your portal")
