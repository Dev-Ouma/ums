"""Create a starter home page that mirrors the hand-written front page.

Gives editors something real to edit rather than a blank CMS, and doubles as
a worked example of every block type. Idempotent: re-running replaces the
seeded page rather than stacking duplicates.
"""
from django.core.management.base import BaseCommand

from cms.models import Block, MenuItem, Page, SiteSettings

BLOCKS = [
    dict(block_type="hero", variant="gradient",
         subheading="Welcome To UMS",
         heading="University Management",
         body="<p>One smart platform to run admissions, academics, attendance, exams, "
              "fees and campus life — with role-based portals and built-in AI insights.</p>",
         link_text="Get Started", link_url="/accounts/signup/",
         secondary_link_text="Browse courses", secondary_link_url="/catalog/"),
    dict(block_type="cards", variant="icons", heading="Choose your portal",
         body="Student Portal | Track attendance, results, assignments and fees. | fa-user-graduate | /accounts/login/\n"
              "Faculty Portal | Take attendance, set assignments and grade work. | fa-chalkboard-user | /accounts/login/\n"
              "Administration | Full control over students, staff and finances. | fa-user-shield | /accounts/login/"),
    dict(block_type="stats", variant="cards", heading="UMS at a glance",
         body="46 | Students\n10 | Faculty\n18 | Courses\n6 | Departments"),
    dict(block_type="features", variant="grid3", heading="Everything in one place",
         subheading="From admission to graduation, without the spreadsheets.",
         body="Admissions | Applications, enrolment and programme placement. | fa-file-circle-plus\n"
              "Attendance | Per-session marking with instant percentages. | fa-calendar-check\n"
              "Examinations | Weighted CATs and finals with published results. | fa-file-pen\n"
              "Fees | Invoices, payments and outstanding balances. | fa-wallet\n"
              "AI Insights | At-risk detection and performance prediction. | fa-wand-magic-sparkles\n"
              "Campus Life | Events and notices for the whole institution. | fa-calendar-days"),
    dict(block_type="departments", variant="grid", heading="Our departments", item_limit=6),
    dict(block_type="courses", variant="grid", heading="Featured courses", item_limit=6),
    dict(block_type="cta", variant="gradient", heading="Ready to modernise your campus?",
         subheading="Join thousands of students and faculty already using UMS every day.",
         link_text="Create free account", link_url="/accounts/signup/",
         secondary_link_text="Talk to us", secondary_link_url="/contact/"),
]

MENUS = [
    ("header", [("Home", "/"), ("About Us", "/about/"), ("Courses", "/catalog/"), ("Apply Now", "/admissions/apply/"), ("Contact", "/contact/")]),
    ("footer_explore", [("Home", "/"), ("Courses", "/catalog/"), ("Apply Now", "/admissions/apply/"), ("About", "/about/"), ("Contact", "/contact/")]),
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
