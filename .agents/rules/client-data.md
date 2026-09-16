---
trigger: always_on
---

# Client Data Rules — Wigot School of Hospitality

## Primary Client
**Wigot School of Hospitality** is the number one client for this system.

## Data Source Rules

1. **Always use live data from the client website** — Never invent course names,
   fees, durations, or contact info from memory.
   Primary source: https://www.wigotschoolofhospitality.com/courses

2. **Use the seed command for all course updates:**
   ```bash
   .venv/bin/python manage.py seed_wigot_courses
   ```
   Update `university/management/commands/seed_wigot_courses.py` before running.

3. **Correct contact information (use exactly):**
   - Address: Mamboleo, Kisumu, Off Kisumu–Kakamega Road
   - Phone: +254 706 063 799 / +254 708 112 222
   - Email: info@wigotschoolofhospitality.com

4. **No placeholder data on client-facing pages** — Never use `hello@example.com`,
   `Knowledge City, Campus Road`, `0000`, or generic addresses in any template
   visible to Wigot School of Hospitality users or administrators.

5. **Wigot Department code is `HOSP`** — All Wigot courses belong to the
   `Hospitality` department with code `HOSP`.

6. **Course images** — Use the official image URLs from the client website.
   Do not hotlink to unrelated or stock images for course cards.

7. **Fee figures are in KES (Kenyan Shillings)** — Always display fees as
   `KSh. X,XXX` or `KES X,XXX`, never as a bare number.
