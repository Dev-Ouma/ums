# Transcripts and academic reports

Open **Examinations → Transcripts & reports**. Students see their own records; administrators can search students by name or registration number. Faculty cannot access full student transcripts under the existing result-statement permission policy.

The provisional transcript defaults to the latest published term and supports term selection. The academic transcript and performance report always cover the full published history. Browser previews embed the PDF endpoint itself; native browser support determines available zoom, search, page and print controls. Open PDF and Download PDF remain available as fallbacks. Endpoints require authentication and return private, non-cacheable responses. PDF framing is restricted to the same origin.

## Record rules

- Only published assessments contribute. Approved but unpublished, withdrawn/reopened, pending and incomplete records are excluded.
- A complete course requires components totaling 100% including CAT and final, or one final assessment with weight 100%. A final weighted at 70% alone is incomplete.
- Grades and grade points use the final examination's saved grade bands. Historical grade-only bands retain the existing system's legacy point mapping.
- GPA and CGPA divide total credit-weighted grade points by attempted credits. Zero credit totals produce N/A. Failed completed attempts remain in the calculation.
- The latest published supplementary sitting (date, then ID) replaces its original component. Separate term attempts remain in GPA; passed-course credits are earned once. The selected term's credits earned are newly earned credits, and its CGPA includes earlier history.
- Transcript, statement and student-dashboard GPA share the same calculation.
- Grading legends come from the included examinations. Different saved scales are listed separately.

## Institutional configuration and certification

Letterheads use existing CMS SiteSettings branding/contact fields and uploaded logos, falling back to the local UMS logo. Academic term labels and dates are displayed as stored. Curriculum year derives from the existing course year convention; programme level comes from the student's programme.

The current schema has no official standing decisions, required degree-credit total, separate academic-year entity, or historical programme/credit snapshots. Reports explicitly show unavailable standing/requirements and use the existing programme/course metadata. Do not treat assigned courses as the degree requirement.

The reference is a reproducible digest of selected academic records and issue date, not a cryptographic signature or public verification service. PDFs are generated from current records, not stored issued snapshots. Preview/download bytes match for unchanged records and issue date; a later correction generates a new record reference. Certification requires the actual Registrar signature and seal. No digital signature or automatic certification is claimed.

Official-style transcripts use restrained document typography; portal pages inherit the existing Quicksand font and role theme. Supporting charts appear only in the performance section/report. PDFs are A4 with repeated course headers and page counts.

## Validation

Run `python manage.py test university.tests_transcripts university.tests_examinations` using the project virtual environment. Coverage includes credit weighting, custom scales, incomplete and unpublished records, repeats, supplements, role isolation, invalid filters, empty states, inline/download equality and long multipage tables. Poppler text assertions run when `pdftotext` is available.
