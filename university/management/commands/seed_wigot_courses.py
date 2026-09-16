import logging
from django.core.management.base import BaseCommand
from university.models import Course, Department

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Seeds courses for Wigot School of Hospitality from Newsletter PDF"

    def handle(self, *args, **options):
        dept, _ = Department.objects.get_or_create(
            code="HOSP", 
            defaults={"name": "Hospitality", "description": "Wigot School of Hospitality"}
        )

        courses_data = [
            {
                "code": "HDCA",
                "title": "Higher Diploma in Culinary Arts (ICM)",
                "description": "2 Years - KES 320,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
            },
            {
                "code": "DCA",
                "title": "Diploma in Culinary Arts (ICM)",
                "description": "1 Year - KES 200,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
            },
            {
                "code": "DFBS",
                "title": "Diploma in Food and Beverage Service (ICM)",
                "description": "1 Year - KES 165,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/VF-06321646379773.jpg",
            },
            {
                "code": "CCAO",
                "title": "Certificate, Catering & Accommodation Operations (KNEC)",
                "description": "1.5 Years - KES 229,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
            },
            {
                "code": "CFBPS",
                "title": "Certificate, Food and Beverage Production & Service (KNEC)",
                "description": "1.5 Years - KES 229,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
            },
            {
                "code": "DFBPM",
                "title": "Diploma, Food and Beverage Production, Sales & Service Management (KNEC)",
                "description": "2 Years - KES 333,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
            },
            {
                "code": "ADHM",
                "title": "Advanced Diploma in Hospitality Management",
                "description": "3 Years - KES 363,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
            },
            {
                "code": "DHM",
                "title": "Diploma in Hospitality Management",
                "description": "2 Years - KES 249,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
            },
            {
                "code": "CHM",
                "title": "Certificate in Hospitality Management",
                "description": "1 Year - KES 135,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
            },
            {
                "code": "PB",
                "title": "Pastry and Bakery (Basic, Intermediate, Advanced)",
                "description": "1 Month - Part Time Course - KES 38,000 per level",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/AS-56101645008020.jpg",
            },
            {
                "code": "HA",
                "title": "Housekeeping and Accommodation",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
            },
            {
                "code": "LOT",
                "title": "Laundry Operations techniques",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
            },
            {
                "code": "FOCS",
                "title": "Front Office Operations & customer service",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
            },
            {
                "code": "BC",
                "title": "Basic Cookery",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
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
