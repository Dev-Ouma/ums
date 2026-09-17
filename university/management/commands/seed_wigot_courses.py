import logging
from django.core.management.base import BaseCommand
from university.models import Course, Department, Program

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Seeds courses and academic programmes for Wigot School of Hospitality from live catalog data"

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
                "duration_value": 2,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "DCA",
                "title": "Diploma in Culinary Arts (ICM)",
                "description": "1 Year - KES 200,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "DFBS",
                "title": "Diploma in Food and Beverage Service (ICM)",
                "description": "1 Year - KES 165,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/VF-06321646379773.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "CCAO",
                "title": "Certificate, Catering & Accommodation Operations (KNEC)",
                "description": "1.5 Years - KES 229,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
                "duration_value": 18,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "CFBPS",
                "title": "Certificate, Food and Beverage Production & Service (KNEC)",
                "description": "1.5 Years - KES 229,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
                "duration_value": 18,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "DFBPM",
                "title": "Diploma, Food and Beverage Production, Sales & Service Management (KNEC)",
                "description": "2 Years - KES 333,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
                "duration_value": 2,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "ADHM",
                "title": "Advanced Diploma in Hospitality Management",
                "description": "3 Years - KES 363,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
                "duration_value": 3,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "DHM",
                "title": "Diploma in Hospitality Management",
                "description": "2 Years - KES 249,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg",
                "duration_value": 2,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "CHM",
                "title": "Certificate in Hospitality Management",
                "description": "1 Year - KES 135,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.YEARS,
            },
            {
                "code": "PB",
                "title": "Pastry and Bakery (Basic, Intermediate, Advanced)",
                "description": "1 Month - Part Time Course - KES 38,000 per level",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/AS-56101645008020.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "HA",
                "title": "Housekeeping and Accommodation",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "LOT",
                "title": "Laundry Operations techniques",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "FOCS",
                "title": "Front Office Operations & customer service",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "BC",
                "title": "Basic Cookery",
                "description": "1 Month - Short Course - KES 30,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg",
                "duration_value": 1,
                "duration_unit": Program.DurationUnit.MONTHS,
            },
            {
                "code": "HLFO",
                "title": "Housekeeping & Laundry Front Office Operations",
                "description": "1.5 Years - KES 229,000",
                "image_url": "https://www.wigotschoolofhospitality.com/assets/images/items/ON-80401646379967.jpg",
                "duration_value": 18,
                "duration_unit": Program.DurationUnit.MONTHS,
            }
        ]

        count = 0
        for c_data in courses_data:
            # 1. Map to or update corresponding Program in HOSP department
            is_diploma = "diploma" in c_data["title"].lower()
            prog_type = Program.ProgramType.DIPLOMA if is_diploma else Program.ProgramType.CERTIFICATE
            level = "DIP" if is_diploma else "CERT"
            prog_code = f"WSH-{c_data['code']}"

            # Check if program exists either as WSH-<CODE> or <CODE>
            existing_prog = Program.objects.filter(
                department=dept,
                code__in=[prog_code, c_data["code"]]
            ).first()

            if existing_prog:
                existing_prog.name = c_data["title"]
                existing_prog.award_title = c_data["title"]
                existing_prog.description = c_data["description"]
                existing_prog.program_type = prog_type
                existing_prog.level = level
                existing_prog.status = Program.Status.ACTIVE
                existing_prog.duration_value = c_data["duration_value"]
                existing_prog.duration_unit = c_data["duration_unit"]
                existing_prog.save()
                program = existing_prog
            else:
                program = Program.objects.create(
                    code=prog_code,
                    name=c_data["title"],
                    department=dept,
                    program_type=prog_type,
                    level=level,
                    award_title=c_data["title"],
                    status=Program.Status.ACTIVE,
                    description=c_data["description"],
                    duration_value=c_data["duration_value"],
                    duration_unit=c_data["duration_unit"],
                )

            # 2. Update or create Course and link it to Program
            course, created = Course.objects.update_or_create(
                code=c_data["code"],
                defaults={
                    "title": c_data["title"],
                    "description": c_data["description"],
                    "image_url": c_data["image_url"],
                    "department": dept,
                    "program": program,
                }
            )
            count += 1
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} course: {course.title} (Program: {program.code})"))
            
        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {count} courses and academic programmes."))
