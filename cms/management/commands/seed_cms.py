"""Create a starter home page that mirrors the hand-written front page.

Gives editors something real to edit rather than a blank CMS, and doubles as
a worked example of every block type. Idempotent: re-running replaces the
seeded page rather than stacking duplicates.
"""
from django.core.management.base import BaseCommand

from cms.models import Block, MenuItem, Page, SiteSettings

BLOCKS = [
    dict(block_type="hero", variant="gradient",
         subheading="Excellence in Culinary Arts & Hospitality Management",
         heading="Pioneering Hospitality, Culinary Arts & Leadership",
         body="<p>Industry-standard, 100% practical training in Mamboleo, Kisumu. "
              "Commercial production kitchens, dual TVETA &amp; ICM (UK) credentials, and guaranteed 5-star hotel attachments.</p>",
         link_text="Apply for Admission", link_url="/admissions/apply/",
         secondary_link_text="Programmes & Courses", secondary_link_url="/catalog/"),
    dict(block_type="cards", variant="icons", heading="Choose your portal",
         body="Student Portal | Track attendance, download fee receipts, view exam results and attachments. | fa-user-graduate | /accounts/login/\n"
              "Faculty Desk | Daily attendance, practical kitchen rubrics, syllabus and grading. | fa-chalkboard-user | /accounts/login/\n"
              "Administration | Comprehensive controls for admissions, fee reconciliation and audits. | fa-user-shield | /accounts/login/\n"
              "Online Admission | Instant application desk with KCSE upload and M-Pesa fee payment. | fa-file-signature | /admissions/apply/"),
    dict(block_type="stats", variant="cards", heading="Wigot at a Glance",
         body="1250 | Graduates & Alumni\n15 | Career Programs\n100% | Hotel Attachment\n4 | Accreditation Bodies"),
    dict(block_type="features", variant="grid3", heading="The Wigot Advantage",
         subheading="Industry-grade practical excellence in Mamboleo, Kisumu.",
         body="Commercial Kitchens | Multi-station production ranges, salamanders & pastry labs. | fa-kitchen-set\n"
              "Dual Global Credentials | Examined by ICM (UK) & KNEC, TVETA licensed. | fa-award\n"
              "Hotel Attachments | 100% placement with Sarova, Serena, PrideInn & luxury lodges. | fa-hotel\n"
              "M-Pesa Fee Portal | Instant fee settlement, installments and digital receipts. | fa-mobile-screen-button\n"
              "Lakeside Campus | Serene campus at the foot of Kajulu Hills next to Wigot Gardens. | fa-tree\n"
              "Dean of Trainees | Comprehensive spiritual, physical and mental student welfare. | fa-shield-heart"),
    dict(block_type="departments", variant="grid", heading="Our Departments", item_limit=6),
    dict(block_type="courses", variant="grid", heading="Featured Programmes & Courses", item_limit=6),
    dict(block_type="cta", variant="gradient", heading="Shape Your Future in World-Class Hospitality",
         subheading="Intakes Open in January, May & September. Start your application online in just 5 minutes.",
         link_text="Apply Online Now", link_url="/admissions/apply/",
         secondary_link_text="Contact Admissions", secondary_link_url="/contact/"),
]

MENUS = [
    ("header", [("Home", "/"), ("About Us", "/about/"), ("Programmes & Courses", "/catalog/"), ("Student Welfare", "/student-welfare/"), ("Gallery", "/gallery/"), ("Contact", "/contact/")]),
    ("footer_explore", [("Home", "/"), ("Programmes & Courses", "/catalog/"), ("Student Welfare", "/student-welfare/"), ("Gallery", "/gallery/"), ("Apply Now", "/admissions/apply/"), ("About", "/about/"), ("Contact", "/contact/")]),
]


class Command(BaseCommand):
    help = "Seed a starter CMS home page, navigation and site settings."

    def add_arguments(self, parser):
        parser.add_argument("--publish", action="store_true",
                            help="Publish the seeded home page immediately.")

    def handle(self, *args, **options):
        SiteSettings.load()

        Page.objects.filter(slug=Page.RESERVED_HOME).delete()
        page = Page.objects.create(
            title="Home", slug=Page.RESERVED_HOME, layout="landing",
            status=Page.PUBLISHED if options["publish"] else Page.DRAFT,
            meta_description="One smart platform to run admissions, academics, attendance, "
                             "exams, fees and campus life.")
        for order, spec in enumerate(BLOCKS):
            Block.objects.create(page=page, order=order, **spec)

        for location, items in MENUS:
            MenuItem.objects.filter(location=location).delete()
            for order, (label, url) in enumerate(items):
                MenuItem.objects.create(label=label, url=url, location=location, order=order)

        state = "published" if options["publish"] else "as a draft"
        self.stdout.write(self.style.SUCCESS(
            f"Seeded home page ({len(BLOCKS)} sections) {state}, plus navigation."))
        if not options["publish"]:
            self.stdout.write(self.style.WARNING(
                "The hand-written front page is still live. Publish the CMS page "
                "(Site CMS → Pages → Publish) to switch over."))
