---
name: pdf
description: >-
  PDF generation and formal report skill for the UMS project. Covers
  source validation before formal report layout, academic transcript
  generation, grading rule tracing, incomplete record handling, and
  PDF styling standards. Activate whenever generating transcripts,
  fee receipts, admission letters, or any formal PDF document.
---

# PDF Generation Skill — UMS Project

## Core Observation (from log #1)
> Authoritative presentation must FOLLOW validated source eligibility
> and calculation rules — never substitute for them. Always trace
> derived fields to their approved data source before applying formal layout.

---

## 1. Source Validation Checklist (BEFORE any formal layout)

Run this mental checklist before generating any formal PDF:

```
For every derived field in the document:
  1. What is the AUTHORITATIVE source model/field?
  2. Is this record in a COMPLETE state for output?
  3. Are there CUSTOM rules (grading scales, policies) that apply?
  4. What happens if the value is NULL / INCOMPLETE / CONTESTED?
  5. Has the record been APPROVED/LOCKED for formal output?
```

### Academic Transcript Validation:
```python
def validate_transcript_eligibility(student_profile):
    """
    Validates all required conditions before generating a formal transcript.
    Returns (is_eligible: bool, issues: list[str])
    """
    issues = []
    
    # 1. Check student record completeness
    if not student_profile.admission_number:
        issues.append("Missing admission number")
    
    # 2. Check for locked/approved grades (not pending)
    pending_grades = Grade.objects.filter(
        student=student_profile,
        status__in=["PENDING", "CONTESTED"]
    )
    if pending_grades.exists():
        issues.append(f"{pending_grades.count()} grade(s) not yet finalized")
    
    # 3. Check for fee clearance (if required for transcript release)
    outstanding_fees = FeeAccount.objects.filter(
        student=student_profile,
        balance__gt=0
    )
    if outstanding_fees.exists():
        issues.append("Outstanding fee balance — transcript hold")
    
    # 4. Check for custom grading policy
    programs_with_custom_grading = student_profile.enrollments.filter(
        course__program__grading_policy__isnull=False
    )
    # These need special grading scale — flag for manual review
    
    return (len(issues) == 0), issues
```

---

## 2. PDF Generation Stack (ACTUAL Implementation)

### The project uses ReportLab (NOT WeasyPrint)
```bash
.venv/bin/pip show reportlab  # confirm version
```

### Shared Design System: `document_design.py`
Every PDF in the system must use the shared brand components — never hand-roll styles:

```python
from university.document_design import (
    ReportDocTemplate,
    document_styles,       # returns (styles, h1, h2, body, small, ...)
    document_fonts,        # registers Quicksand TTF from static/fonts/quicksand/
    PageNumberCanvas,      # adds page numbers to every page
    letterhead,            # institution logo + name header
    get_branding,          # returns {primary_color, logo_path, institution_name}
    finish_worksheet,      # applies styling to Excel sheets
    make_qr_code_flowable, # generates QR code for document verification
)
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, Table, TableStyle, Spacer
```

### Two Brand Palettes (from `document_design.py`):
- **Standard palette** — matches `static/css/ums.css --primary` — for regular reports
- **Formal palette** — muted, low-color, authoritative — for certified transcripts

### Quicksand Font (embedded in every PDF):
```python
# Called automatically by document_design.document_fonts()
# Fonts loaded from: static/fonts/quicksand/Quicksand.ttf etc.
# Always call document_fonts() before building any PDF
font_normal, font_bold = document_fonts()
```

### QR Verification Code:
```python
# Add QR code to certified documents for verification
qr_flowable = make_qr_code_flowable(
    data=f"https://yourdomain.com/verify/{document_hash}",
    size=60
)
```

---

## 4. Actual Transcript Builder (from transcript_io.py)

The real transcript context builder uses the `examination_services.student_statement()` workflow:

```python
from university import examination_services as workflow

def build_transcript_context(student, term=None, academic_year=None):
    statement = workflow.student_statement(student)
    complete = [g for g in statement.groups if g['complete']]
    
    # Groups by term, sorts by start_date
    by_term = {}
    for g in complete:
        by_term.setdefault(g['term'].pk if g['term'] else None, []).append(g)
    
    semesters = []
    running = []
    earned = {}
    for groups in sorted(by_term.values(), ...):
        # GPA is credit-weighted, CGPA is cumulative
        term_gpa = workflow.weighted_gpa(groups)
        cumulative_gpa = workflow.weighted_gpa(running)
        semesters.append(dict(
            term=groups[0]['term'],
            groups=groups,
            term_gpa=term_gpa,
            cumulative_gpa=cumulative_gpa,
            credits_attempted=sum(g['course'].credits for g in groups),
            credits_completed=new_credits,
        ))
    
    # Per-exam grading legends (each exam has its OWN GradingScale)
    legends = []  # [{key, courses, bands}]
    # ...one legend entry per unique grading scale
```

### GPA Rules (from transcript NOTES constant):
> GPA is the credit-weighted sum of grade points divided by attempted credits.  
> CGPA applies the same formula cumulatively across all completed sessions.  
> Published supplementary or repeat assessments **replace** original grades.  
> Credits are earned **once per passed course unit**.  
> Grades use **each examination's saved grading scale** (not a global scale).

---

## 4. Formal Document Types in UMS (with actual URL patterns)

| Document | Module | URL pattern |
|---|---|---|
| Academic Transcript | `transcript_io.py` | `/students/<pk>/transcript/pdf/` |
| Fee Receipt | `fee_io.py` | `/fees/<pk>/receipt/pdf/` |
| Admission Letter | `admission_document_services.py` | `/admissions/<pk>/letter/pdf/` |
| Industrial Attachment Intro Letter | `attachment_views.py` | `/academics/attachment/<pk>/letter/pdf/` |
| Attachment Logbook | `attachment_views.py` | `/academics/attachment/<pk>/logbook/pdf/` |
| Exam Results Slip | `examination_views.py` | `/manage/academics/examinations/<pk>/results/pdf/` |
| Progressive Report | `progressive_report_io.py` | `/students/<pk>/progressive-report/pdf/` |
| Timetable Export | `timetable_io.py` | `/manage/academics/timetable/export/` |
| Faculty/Student Lists | `faculty_io.py` / `student_io.py` | Various export endpoints |

---

## 5. PDF URL Pattern Standard

```python
# All PDF endpoints follow: /path/<pk>/pdf/
path("students/<int:pk>/transcript/pdf/", transcript_pdf, name="transcript_pdf"),
path("fees/<int:pk>/receipt/pdf/", fee_receipt_pdf, name="fee_receipt_pdf"),
path("attachments/<int:pk>/letter/pdf/", attachment_letter_pdf, name="attachment_intro_letter_pdf"),
```

### PDF View Security:
```python
@login_required
@permission_required("university.view_transcript", raise_exception=True)
def transcript_pdf(request, pk):
    student = get_object_or_404(StudentProfile, pk=pk)
    
    # Extra ownership check for students accessing own transcript
    if request.user.has_role("student"):
        if student.user != request.user:
            raise PermissionDenied("You can only access your own transcript.")
    
    is_eligible, issues = validate_transcript_eligibility(student)
    if not is_eligible:
        messages.error(request, "Transcript cannot be generated: " + "; ".join(issues))
        return redirect("university:student_profile", pk=pk)
    
    return generate_transcript_pdf(request, student)
```

---

## 6. Pre-Release PDF Checklist

- [ ] Source data validated before layout (eligibility check runs first)
- [ ] All NULL/incomplete fields handled explicitly — no empty cells in formal output
- [ ] Grading policy traced to its approved source for each enrollment
- [ ] Document includes generation timestamp, student ID, and authorized signature placeholder
- [ ] PDF download tested at multiple screen sizes
- [ ] PDF URL secured with `@login_required` + `@permission_required`
- [ ] Fee hold check included for transcript/academic documents
