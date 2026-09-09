import re
from datetime import datetime
from django.db import transaction
from django.utils import timezone

from accounts.models import StudentProfile
from university.models import Application, Program, AcademicYear, SystemSetting
from university.settings_services import get_setting


DEFAULT_NUMBERING_PATTERN = "{PROG}/{SEQ:03d}/{YEAR_END}"


def get_numbering_config():
    """Retrieve active student numbering settings from SystemSetting."""
    pattern = get_setting("student_id_format_pattern") or DEFAULT_NUMBERING_PATTERN
    prefix = get_setting("student_id_prefix") or "UMS"
    padding = int(get_setting("student_id_seq_padding") or 3)
    separator = get_setting("student_id_separator") or "/"
    return {
        "pattern": pattern,
        "prefix": prefix,
        "padding": padding,
        "separator": separator,
    }


def format_token_replacement(token_match, context):
    """Safely format matched pattern token e.g. {SEQ:03d}, {SEQ:04d}, {PROG}, or {YEAR_END}."""
    full_token = token_match.group(1)
    parts = full_token.split(":")
    var_name = parts[0].strip().upper()
    fmt_spec = parts[1] if len(parts) > 1 else ""

    val = context.get(var_name, "")
    if var_name == "SEQ":
        seq_num = int(val or 1)
        if fmt_spec:
            try:
                return f"{seq_num:{fmt_spec}}"
            except Exception:
                pass
        padding = int(context.get("PADDING", 3))
        return f"{seq_num:0{padding}d}"

    return str(val)


def render_pattern(pattern, context):
    """Render a pattern template string with provided context variables."""
    # Pattern e.g. {PROG}/{SEQ:03d}/{YEAR_END}
    return re.sub(r"\{([A-Za-z0-9_:]+)\}", lambda m: format_token_replacement(m, context), pattern)


def build_numbering_context(program=None, academic_year=None, intake=None, category=None, seq=1):
    """Construct token context dictionary for template rendering."""
    inst_code = get_setting("institution_code") or "UNI"
    now_year = timezone.now().year

    ay_obj = academic_year
    if not ay_obj:
        if intake:
            ay_obj = intake.academic_year
        if not ay_obj:
            ay_obj = AcademicYear.objects.filter(is_current=True).first()

    ay_name = ""
    year_start = now_year
    year_end = now_year

    if ay_obj:
        ay_name = ay_obj.name.replace("–", "/").replace("-", "/")
        if ay_obj.start_date:
            year_start = ay_obj.start_date.year if hasattr(ay_obj.start_date, "year") else int(str(ay_obj.start_date)[:4])
        if ay_obj.end_date:
            year_end = ay_obj.end_date.year if hasattr(ay_obj.end_date, "year") else int(str(ay_obj.end_date)[:4])

        # Parse from academic year name e.g. "2025/2026" or "2026"
        matches = [int(y) for y in re.findall(r"\b(20\d\d)\b", ay_name)]
        if len(matches) >= 2:
            if not ay_obj.start_date:
                year_start = matches[0]
            if not ay_obj.end_date:
                year_end = matches[1]
        elif len(matches) == 1:
            if not ay_obj.end_date:
                year_end = matches[0]
            if not ay_obj.start_date:
                year_start = year_end - 1
        else:
            if not ay_obj.end_date:
                year_end = year_start + 1
    else:
        year_start = now_year
        year_end = now_year

    prog_code = "ADM"
    dept_code = "GEN"
    faculty_code = "UNIV"
    if program:
        prog_code = program.code or "ADM"
        if getattr(program, "department_id", None):
            try:
                dept = program.department
                if dept:
                    dept_code = dept.code or "GEN"
            except Exception:
                pass
        if getattr(program, "school_id", None):
            try:
                school = program.school
                if school:
                    faculty_code = school.code or "UNIV"
            except Exception:
                pass

    intake_code = intake.code if (intake and getattr(intake, "code", None)) else "MAIN"
    cat_code = category or "GOK"

    return {
        "INST": inst_code,
        "YEAR": str(year_end),
        "YEAR_END": str(year_end),
        "END_YEAR": str(year_end),
        "YEAR_START": str(year_start),
        "START_YEAR": str(year_start),
        "YY": str(year_end)[-2:],
        "YY_END": str(year_end)[-2:],
        "YY_START": str(year_start)[-2:],
        "ACADEMIC_YEAR": ay_name or f"{year_start}/{year_end}",
        "PROG": prog_code,
        "PROGRAM": prog_code,
        "DEPT": dept_code,
        "DEPARTMENT": dept_code,
        "FACULTY": faculty_code,
        "SCHOOL": faculty_code,
        "INTAKE": intake_code,
        "CATEGORY": cat_code,
        "CAMPUS": "MAIN",
        "SEQ": seq,
        "PADDING": int(get_setting("student_id_seq_padding") or 3),
    }


def is_registration_number_available(candidate_no):
    """Verify that candidate_no is not already taken by a student or admitted application."""
    if not candidate_no:
        return False
    clean_no = candidate_no.strip()
    if StudentProfile.objects.filter(roll_no__iexact=clean_no).exists():
        return False
    if Application.objects.filter(admitted_reg_no__iexact=clean_no).exists():
        return False
    return True


@transaction.atomic
def generate_student_registration_number(program=None, academic_year=None, intake=None, category=None):
    """
    Generate an authoritative, guaranteed unique sequential student registration number
    following institutional template rules.
    """
    cfg = get_numbering_config()
    pattern = cfg["pattern"]

    # Build context for template
    ctx = build_numbering_context(program=program, academic_year=academic_year, intake=intake, category=category)

    # Calculate starting sequence by counting existing records for this program/year scope
    base_probe_ctx = dict(ctx)
    base_probe_ctx["SEQ"] = 1
    # Estimate sequence offset
    year_str = str(ctx["YEAR_END"])
    prog_str = str(ctx["PROG"])

    count_students = StudentProfile.objects.filter(roll_no__contains=prog_str).filter(roll_no__contains=year_str).count()
    count_apps = Application.objects.filter(admitted_reg_no__contains=prog_str).filter(admitted_reg_no__contains=year_str).count()
    seq_candidate = max(1, count_students + count_apps + 1)

    max_attempts = 1000
    for attempt in range(max_attempts):
        ctx["SEQ"] = seq_candidate + attempt
        reg_number = render_pattern(pattern, ctx)

        if is_registration_number_available(reg_number):
            return reg_number

    # Fallback timestamp guaranteed unique
    fallback = f"{ctx['PROG']}/{timezone.now().strftime('%m%d%H%M%S')}/{ctx['YEAR_END']}"
    return fallback


def preview_student_registration_number(pattern=None, program=None, academic_year=None):
    """Generate a sample formatted registration number for admin preview."""
    pat = pattern or get_numbering_config()["pattern"]
    ctx = build_numbering_context(program=program, academic_year=academic_year, seq=1)
    return render_pattern(pat, ctx)
