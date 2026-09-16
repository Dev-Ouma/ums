---
name: client-data-management
description: >-
  Client data sourcing and management skill for the UMS project.
  Wigot School of Hospitality is the primary client. Covers how to
  fetch, update, and seed authoritative client data from live sources,
  with standards for course management, contact info, and data freshness.
  Activate whenever working with Wigot School of Hospitality data.
---

# Client Data Management Skill — Wigot School of Hospitality

## Primary Client
**Wigot School of Hospitality**  
📍 Mamboleo, Kisumu, Off Kisumu–Kakamega Road  
📞 +254 706 063 799 / +254 708 112 222  
📧 info@wigotschoolofhospitality.com  
🌐 https://www.wigotschoolofhospitality.com

---

## 1. Authoritative Data Sources

Always fetch client data from these sources — never guess or hardcode from memory:

| Data Type | Source URL |
|---|---|
| Courses list | https://www.wigotschoolofhospitality.com/courses |
| Course details | https://www.wigotschoolofhospitality.com/courses/<slug> |
| Admission info | https://www.wigotschoolofhospitality.com/admission |
| Gallery | https://www.wigotschoolofhospitality.com/gallery |
| Newsletter PDF | https://www.wigotschoolofhospitality.com/assets/images/resources/ |
| Contact details | https://www.wigotschoolofhospitality.com/contact-us |

---

## 2. Course Data — Current Catalog (from newsletter PDF)

| Code | Course Name | Duration | Type | Fee (KES) | Curriculum |
|---|---|---|---|---|---|
| HDCA | Higher Diploma in Culinary Arts | 2 Years | Full Time | 320,000 | ICM |
| DCA | Diploma in Culinary Arts | 1 Year | Full Time | 200,000 | ICM |
| DFBS | Diploma in Food & Beverage Service | 1 Year | Full Time | 165,000 | ICM |
| CCAO | Certificate, Catering & Accommodation Operations | 1.5 Years | Full Time | 229,000 | KNEC |
| CFBPS | Certificate, Food & Beverage Production & Service | 1.5 Years | Full Time | 229,000 | KNEC |
| DFBPM | Diploma, Food & Beverage Production, Sales & Service Mgmt | 2 Years | Full Time | 333,000 | KNEC |
| ADHM | Advanced Diploma in Hospitality Management | 3 Years | Full Time | 363,000 | — |
| DHM | Diploma in Hospitality Management | 2 Years | Full Time | 249,000 | — |
| CHM | Certificate in Hospitality Management | 1 Year | Full Time | 135,000 | — |
| PB | Pastry & Bakery (Basic, Intermediate, Advanced) | 1 Month/level | Part Time | 38,000/level | Internal |
| HA | Housekeeping & Accommodation | 1 Month | Short Course | 30,000 | — |
| LOT | Laundry Operations Techniques | 1 Month | Short Course | 30,000 | — |
| FOCS | Front Office Operations & Customer Service | 1 Month | Short Course | 30,000 | — |
| BC | Basic Cookery | 1 Month | Short Course | 30,000 | — |

---

## 3. Seeding / Updating Course Data

The management command handles all course seeding:

```bash
# Run to seed/update all Wigot courses
.venv/bin/python manage.py seed_wigot_courses

# File location
university/management/commands/seed_wigot_courses.py
```

### When to re-run the seed command:
- After fetching updated course info from the live website
- After receiving a new newsletter PDF from the client
- After any new course is announced

### Updating the seed command when courses change:
1. Visit https://www.wigotschoolofhospitality.com/courses
2. Check the latest newsletter PDF at the resources URL above
3. Update `seed_wigot_courses.py` with new course data
4. Run the command: `.venv/bin/python manage.py seed_wigot_courses`
5. Commit: `data(courses): update Wigot course catalog from [source]`

---

## 4. Department Setup

Wigot courses all belong to the `HOSP` department:

```python
dept, _ = Department.objects.get_or_create(
    code="HOSP",
    defaults={"name": "Hospitality", "description": "Wigot School of Hospitality"}
)
```

---

## 5. Contact Information Standards

When using Wigot contact details in templates or views, always use:

```
Address: Mamboleo, Kisumu, Off Kisumu–Kakamega Road
Phone 1: +254 706 063 799
Phone 2: +254 708 112 222
Email:   info@wigotschoolofhospitality.com
Website: https://www.wigotschoolofhospitality.com
```

Never use placeholder data (`hello@example.com`, `0000`, `Knowledge City`)
in any Wigot-facing page or template.

---

## 6. Image Assets

Course images are hosted on the Wigot website. Use these direct URLs:

```python
# Available course image assets
WIGOT_IMAGE_BASE = "https://www.wigotschoolofhospitality.com/assets/images/items/"

COURSE_IMAGES = {
    "culinary":      f"{WIGOT_IMAGE_BASE}MN-24261646379768.jpg",
    "food_beverage": f"{WIGOT_IMAGE_BASE}VF-06321646379773.jpg",
    "accommodation": f"{WIGOT_IMAGE_BASE}YW-68711646379797.jpg",
    "pastry":        f"{WIGOT_IMAGE_BASE}AS-56101645008020.jpg",
    "housekeeping":  f"{WIGOT_IMAGE_BASE}ON-80401646379967.jpg",
    "management":    f"{WIGOT_IMAGE_BASE}LZ-33781646917154.jpg",
}
```

---

## 7. Data Freshness Policy

| Data Type | Review Frequency | Source |
|---|---|---|
| Course list & fees | Each intake/term | Live website + newsletter |
| Contact information | Quarterly | Live website |
| Gallery/images | When client updates | Live website |
| Admissions requirements | Each intake | Admissions page |
