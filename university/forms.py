from django import forms
from django.utils import timezone

from accounts.models import FacultyProfile, Role, StudentProfile, User

from .models import (
    Assignment, ClassSchedule, Course, Department, Enrollment, Event, Exam,
    ExamRoom, FeeInvoice, FeeStructure, Notice, Program, StudentRequest,
    AcademicYear, AcademicTerm,
)

CTRL = "form-control"
SEL = "form-select"


def _style(fields, widgets=None):
    """Apply Bootstrap classes to a set of bound form fields."""
    for name, field in fields.items():
        w = field.widget
        if isinstance(w, (forms.Select, forms.SelectMultiple)):
            w.attrs.setdefault("class", SEL)
        elif isinstance(w, forms.CheckboxInput):
            w.attrs.setdefault("class", "form-check-input")
        elif isinstance(w, (forms.DateInput, forms.DateTimeInput)):
            w.attrs.setdefault("class", CTRL)
            w.attrs.setdefault("type", "date")
        else:
            w.attrs.setdefault("class", CTRL)


# ==========================================================================
# STUDENT  (User + StudentProfile combined)
# ==========================================================================
class StudentForm(forms.Form):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, initial="0000", required=False)
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text="Leave blank to email a single-use activation link "
                                         "instead, or to keep the current password on edit.")
    roll_no = forms.CharField(max_length=20)
    program = forms.ModelChoiceField(queryset=Program.objects.all(), required=False)
    current_semester = forms.IntegerField(min_value=1, max_value=12, initial=1)
    gender = forms.ChoiceField(choices=StudentProfile.GENDER, initial="O")
    address = forms.CharField(max_length=255, required=False)
    guardian_name = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance:
            u = instance.user
            kwargs.setdefault("initial", {}).update({
                "first_name": u.first_name, "last_name": u.last_name,
                "email": u.email, "phone": u.phone, "username": u.username,
                "roll_no": instance.roll_no, "program": instance.program_id,
                "current_semester": instance.current_semester, "gender": instance.gender,
                "address": instance.address, "guardian_name": instance.guardian_name,
            })
        super().__init__(*args, **kwargs)
        _style(self.fields)
        if instance:
            self.fields["username"].widget.attrs["readonly"] = True

    def clean_username(self):
        username = self.cleaned_data["username"]
        qs = User.objects.filter(username__iexact=username)
        if self.instance:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean_roll_no(self):
        roll = self.cleaned_data["roll_no"]
        qs = StudentProfile.objects.filter(roll_no__iexact=roll)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Roll number already exists.")
        return roll

    def clean(self):
        """
        A password is optional. Left blank on a new record, the account is
        created with an activation link instead, which is the safer default.
        Supplied, it has to satisfy the institutional password policy.
        """
        cleaned = super().clean()
        password = cleaned.get("password")
        if password:
            from university.identity_services import validate_password
            for message in validate_password(password):
                self.add_error("password", message)
        return cleaned

    def save(self, actor=None):
        from university.identity_models import UserType
        from university.identity_services import (
            create_user_account, provision_student_account, record_password_change,
        )
        d = self.cleaned_data
        if self.instance:
            u = self.instance.user
            sp = self.instance
            u.first_name = d["first_name"]
            u.last_name = d["last_name"]
            u.email = d["email"]
            u.phone = d["phone"] or "0000"
            u.role = Role.STUDENT
            u.save()
            if d.get("password"):
                # Goes through the central service so password history, the audit
                # entry and the forced-change flag all stay consistent.
                record_password_change(u, d["password"], actor=actor,
                                       reason="Administrator edit of student record")
        else:
            created = create_user_account(
                user_type=UserType.STUDENT,
                first_name=d["first_name"],
                last_name=d["last_name"],
                email=d["email"],
                username=d["username"],
                phone=d["phone"] or "0000",
                role=Role.STUDENT,
                password_mode="MANUAL" if d.get("password") else "LINK",
                password=d.get("password") or None,
                actor=actor,
                notify=True,
            )
            u = created["user"]
            sp = StudentProfile(user=u)
        sp.user = u
        sp.roll_no = d["roll_no"]
        sp.program = d["program"]
        sp.current_semester = d["current_semester"]
        sp.gender = d["gender"]
        sp.address = d["address"] or "Campus Hostel Block"
        sp.guardian_name = d["guardian_name"] or "Guardian"
        if not sp.admission_date:
            sp.admission_date = timezone.now().date()
        sp.save()
        if not self.instance:
            provision_student_account(sp, actor=actor, notify=False)
        return sp


# ==========================================================================
# FACULTY  (User + FacultyProfile combined)
# ==========================================================================
class FacultyForm(forms.Form):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, initial="0000", required=False)
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text="Leave blank to email a single-use activation link "
                                         "instead, or to keep the current password on edit.")
    employee_id = forms.CharField(max_length=20)
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False)
    designation = forms.CharField(max_length=80, initial="Assistant Professor")
    specialization = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance:
            u = instance.user
            kwargs.setdefault("initial", {}).update({
                "first_name": u.first_name, "last_name": u.last_name,
                "email": u.email, "phone": u.phone, "username": u.username,
                "employee_id": instance.employee_id, "department": instance.department_id,
                "designation": instance.designation, "specialization": instance.specialization,
            })
        super().__init__(*args, **kwargs)
        _style(self.fields)
        if instance:
            self.fields["username"].widget.attrs["readonly"] = True

    def clean_username(self):
        username = self.cleaned_data["username"]
        qs = User.objects.filter(username__iexact=username)
        if self.instance:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean_employee_id(self):
        eid = self.cleaned_data["employee_id"]
        qs = FacultyProfile.objects.filter(employee_id__iexact=eid)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Employee ID already exists.")
        return eid

    def clean(self):
        """Same rule as students: a blank password means an activation link."""
        cleaned = super().clean()
        password = cleaned.get("password")
        if password:
            from university.identity_services import validate_password
            for message in validate_password(password):
                self.add_error("password", message)
        return cleaned

    def save(self, actor=None):
        from university.identity_models import UserType
        from university.identity_services import (
            create_user_account, provision_staff_account, record_password_change,
        )
        d = self.cleaned_data
        if self.instance:
            u = self.instance.user
            fp = self.instance
            u.first_name = d["first_name"]
            u.last_name = d["last_name"]
            u.email = d["email"]
            u.phone = d["phone"] or "0000"
            u.role = Role.FACULTY
            u.save()
            if d.get("password"):
                record_password_change(u, d["password"], actor=actor,
                                       reason="Administrator edit of staff record")
        else:
            created = create_user_account(
                user_type=UserType.STAFF,
                first_name=d["first_name"],
                last_name=d["last_name"],
                email=d["email"],
                username=d["username"],
                phone=d["phone"] or "0000",
                role=Role.FACULTY,
                password_mode="MANUAL" if d.get("password") else "LINK",
                password=d.get("password") or None,
                actor=actor,
                notify=True,
            )
            u = created["user"]
            fp = FacultyProfile(user=u)
        fp.user = u
        fp.employee_id = d["employee_id"]
        fp.department = d["department"]
        fp.designation = d["designation"]
        fp.specialization = d["specialization"]
        if not fp.joining_date:
            fp.joining_date = timezone.now().date()
        fp.save()
        if not self.instance:
            provision_staff_account(fp, actor=actor, notify=False)
        return fp


# ==========================================================================
# Simple ModelForms
# ==========================================================================
class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "code", "description", "icon", "color", "image_url"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "color": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = [
            "code", "name", "award_title", "department", "program_type", "level",
            "study_mode", "duration_value", "duration_unit", "min_credits", "max_credits",
            "total_seats", "status", "description", "career_prospects"
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Overview and learning outcomes..."}),
            "career_prospects": forms.Textarea(attrs={"rows": 3, "placeholder": "Career pathways and professional opportunities..."}),
            "award_title": forms.TextInput(attrs={"placeholder": "e.g. Bachelor of Science in Computer Science"}),
        }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)
        self.fields["department"].queryset = Department.objects.select_related("school").order_by("school__name", "name")

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        qs = Program.objects.filter(code__iexact=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Programme Code '{code}' is already registered in the system.")
        return code


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "title", "department", "program", "faculty", "credits",
                  "semester_no", "status", "description", "image_url"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "semester_no": forms.Select(choices=[(1, "Semester 1"), (2, "Semester 2"), (3, "Semester 3")]),
        }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["course", "title", "description", "max_marks", "due_date"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, faculty=None, **k):
        super().__init__(*a, **k)
        if faculty is not None:
            self.fields["course"].queryset = Course.objects.filter(faculty=faculty)
        _style(self.fields)


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ["course", "term", "name", "date", "max_marks"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ["title", "description", "category", "location", "date", "icon", "image_url"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class FeeInvoiceForm(forms.ModelForm):
    class Meta:
        model = FeeInvoice
        fields = ["student", "term", "title", "amount", "amount_paid", "due_date"]
        labels = {"amount": "Amount (KES)", "amount_paid": "Amount paid (KES)"}
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class ClassScheduleForm(forms.ModelForm):
    class Meta:
        model = ClassSchedule
        fields = ["course", "term", "room", "day", "start_time", "end_time", "session_type", "status"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["room"].queryset = ExamRoom.objects.filter(active=True)
        _style(self.fields)


class StudentRequestForm(forms.ModelForm):
    class Meta:
        model = StudentRequest
        fields = ["request_type", "reason", "start_date", "end_date", "supporting_document"]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 4}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "start_date": "Start date",
            "end_date": "Expected end / resumption date",
        }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)
        self.fields["supporting_document"].required = False

    def clean(self):
        cleaned = super().clean()
        rtype = cleaned.get("request_type")
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if rtype in (StudentRequest.Type.DEFERMENT, StudentRequest.Type.SICK_LEAVE):
            if not start or not end:
                raise forms.ValidationError(
                    "Start and end dates are required for deferment and sick leave requests.")
            if end < start:
                raise forms.ValidationError("The end date must be on or after the start date.")
        return cleaned


class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = [
            "program", "term", "year_of_study", "semester",
            "tuition_fee", "registration_fee", "examination_fee",
            "library_fee", "activity_fee", "medical_fee",
            "ict_fee", "student_union_fee"
        ]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _style(self.fields)


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = [
            "name", "code", "start_date", "end_date", "status",
            "is_current", "reference_no", "max_programmes_allowed", "description",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["code"].required = False
        self.fields["reference_no"].required = False
        self.fields["description"].required = False

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and start >= end:
            self.add_error("end_date", "Academic year end date must be strictly after start date.")
        return cleaned


class SemesterForm(forms.ModelForm):
    class Meta:
        model = AcademicTerm
        fields = [
            "name", "academic_year", "term_type", "semester_number",
            "start_date", "end_date", "registration_start_date",
            "registration_end_date", "exam_start_date", "exam_end_date",
            "status", "is_current",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "registration_start_date": forms.DateInput(attrs={"type": "date"}),
            "registration_end_date": forms.DateInput(attrs={"type": "date"}),
            "exam_start_date": forms.DateInput(attrs={"type": "date"}),
            "exam_end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["registration_start_date"].required = False
        self.fields["registration_end_date"].required = False
        self.fields["exam_start_date"].required = False
        self.fields["exam_end_date"].required = False

    def clean(self):
        cleaned = super().clean()
        ay = cleaned.get("academic_year")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        reg_start = cleaned.get("registration_start_date")
        reg_end = cleaned.get("registration_end_date")
        exam_start = cleaned.get("exam_start_date")
        exam_end = cleaned.get("exam_end_date")

        if start and end and start >= end:
            self.add_error("end_date", "Semester end date must be strictly after start date.")

        if ay and start and end:
            if start < ay.start_date or end > ay.end_date:
                self.add_error(
                    "start_date",
                    f"Semester dates must fall within parent Academic Year ({ay.start_date} to {ay.end_date})."
                )

        if reg_start and reg_end and reg_start > reg_end:
            self.add_error("registration_end_date", "Registration cut-off date must be on or after start date.")

        if exam_start and exam_end and exam_start > exam_end:
            self.add_error("exam_end_date", "Exam concluding date must be on or after start date.")

        return cleaned

