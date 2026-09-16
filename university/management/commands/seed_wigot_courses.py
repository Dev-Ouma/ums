import logging
from django.core.management.base import BaseCommand
from university.models import Course, Department

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Seeds courses for Wigot School of Hospitality"

    def handle(self, *args, **options):
        # Create or get department
        dept, _ = Department.objects.get_or_create(
            code="HOSP", 
            defaults={"name": "Hospitality", "description": "Wigot School of Hospitality"}
        )

        courses_data = [
            {
                "code": "DCA",
                "title": "Diploma Culinary Arts",
                "description": "1 Year - Full Time Course - ICM Curriculum - KSh. 200,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
            },
            {
                "code": "DFBS",
                "title": "Diploma Food & Beverage Service",
                "description": "1 Year - Full Time Course - ICM Curriculum - KSh. 165,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/VF-06321646379773.jpg",
            },
            {
                "code": "CCAO",
                "title": "Certificate, Catering & Accommodation Operations",
                "description": "2 Years - Full Time Course - KNEC Curriculum - KSh. 209,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
            },
            {
                "code": "PB",
                "title": "Pastry & Bakery (Basic, Intermediate & Advanced)",
                "description": "1 Month - Part Time Course - Internal Curriculum - KSh. 58,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/AS-56101645008020.jpg",
            },
            {
                "code": "HLFO",
                "title": "Housekeeping & Laundry Front Office Operations",
                "description": "1 Month - Part Time Course - Internal Curriculum - KSh. 38,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
            },
            {
                "code": "DFBPM",
                "title": "Diploma in Food & Beverage Production, Sales & Service Management",
                "description": "3 Years - Full Time Course - KNEC Curriculum - KSh. 273,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
            }
        ]

        count = 0
        for c_data in courses_data:
            course, created = Course.objects.update_or_create(
                code=c_data["code"],
                defaults={
                    "title": c_data["title"],
                    "description": c_data["description"],
                    "image_url": c_data["image_url"],
                    "department": dept,
                }
            )
            count += 1
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} course: {course.title}"))
            
        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {count} courses."))
