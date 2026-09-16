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

## 2. PDF Generation Libraries

### Current Stack:
The project uses **WeasyPrint** or **ReportLab** (check `requirements.txt`).

```bash
# Check what's installed
.venv/bin/pip show weasyprint reportlab xhtml2pdf
```

### Recommended: WeasyPrint (HTML → PDF)
Best for transcript/letter formats — renders your existing HTML templates:

```python
from weasyprint import HTML
from django.template.loader import render_to_string

def generate_transcript_pdf(request, student_profile):
    context = build_transcript_context(student_profile)
    html_string = render_to_string("pdfs/transcript.html", context, request=request)
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="transcript_{student_profile.admission_number}.pdf"'
    )
    return response
```

---

## 3. Handling Incomplete Data

**NEVER render a formal PDF with missing or null fields.**  
Instead, display explicit placeholders or block generation.

```python
def build_transcript_context(student_profile):
    is_eligible, issues = validate_transcript_eligibility(student_profile)
    
    if not is_eligible:
        raise ValueError(f"Transcript not eligible for generation: {'; '.join(issues)}")
    
    enrollments = Enrollment.objects.filter(
        student=student_profile,
        status="COMPLETED"
    ).select_related("course", "course__program", "grade")
    
    return {
        "student": student_profile,
        "enrollments": enrollments,
        "generated_at": timezone.now(),
        "generated_by": "UMS Academic Records System",
        # Explicitly mark if any field is unavailable
        "grading_policy": student_profile.program.grading_policy if student_profile.program else None,
        "policy_unavailable": student_profile.program is None,
    }
```

### In Template — Explicit Unavailability:
```html
{% if policy_unavailable %}
  <div class="disclaimer">
    ⚠ Grading policy not available for this programme. 
    Contact the registrar for clarification.
  </div>
{% else %}
  <!-- Normal grade rendering -->
{% endif %}

{% for enrollment in enrollments %}
  <tr>
    <td>{{ enrollment.course.code }}</td>
    <td>{{ enrollment.course.title }}</td>
    <td>
      {% if enrollment.grade %}
        {{ enrollment.grade.letter_grade }} ({{ enrollment.grade.score }})
      {% else %}
        <em>Grade not yet recorded</em>
      {% endif %}
    </td>
  </tr>
{% endfor %}
```

---

## 4. Formal Document Types in UMS

| Document | Template | Trigger |
|---|---|---|
| Academic Transcript | `pdfs/transcript.html` | Student/Admin request |
| Fee Receipt | `pdfs/fee_receipt.html` | After payment confirmation |
| Admission Letter | `pdfs/admission_letter.html` | After admission approval |
| Industrial Attachment Letter | `pdfs/attachment_intro.html` | After attachment approval |
| Logbook | `pdfs/attachment_logbook.html` | Student submission |
| Exam Results Slip | `pdfs/results_slip.html` | After results are published |

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
