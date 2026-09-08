from django import forms
from accounts.models import FacultyProfile
from .models import AcademicTerm, Course, Exam, ExamRoom, GradingScale, default_grade_bands


class StyledForm:
    def style(self):
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'


class ExaminationForm(StyledForm, forms.ModelForm):
    class Meta:
        model = Exam
        fields = [
            'course', 'term', 'name', 'kind', 'original_exam', 'date',
            'start_time', 'end_time', 'room', 'invigilator',
            'internal_examiner', 'external_examiner', 'external_examiner_name',
            'cat_max_marks', 'exam_max_marks', 'max_marks', 'pass_mark', 'instructions'
        ]
        labels = {
            'weight': 'Contribution to course total (%)',
            'pass_mark': 'Pass threshold (%)',
            'cat_max_marks': 'CAT Maximum Marks',
            'exam_max_marks': 'Final Exam Maximum Marks',
            'internal_examiner': 'Internal Examiner (IE)',
            'external_examiner': 'External Examiner (EE)',
            'external_examiner_name': 'External Examiner Name / Institution',
        }
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
            'instructions': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'internal_examiner': 'Automatically defaulted to the course lecturer; can be reassigned when required.',
            'external_examiner': 'Optional external examiner or moderator assigned for oversight.',
            'cat_max_marks': 'Maximum marks achievable in the Continuous Assessment Test (typically 30).',
            'exam_max_marks': 'Maximum marks achievable in the Final Examination (typically 70).',
            'weight': 'Regular assessments for one course and term may total at most 100%.',
            'original_exam': 'Select only for a supplementary sitting. Candidates are drawn from failed or absent results.',
            'pass_mark': 'CUE minimum passing grade threshold (typically 40% for Grade D).',
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        courses = Course.objects.select_related('department', 'faculty__user')
        if not (user.is_admin_role or user.is_superuser):
            courses = courses.filter(faculty__user=user)
        self.fields['course'].queryset = courses
        self.fields['term'].required = True
        self.fields['kind'].choices = [(k, v) for k, v in Exam.Kind.choices if k != 'PRACTICAL']
        self.fields['room'].queryset = ExamRoom.objects.filter(active=True)
        self.fields['original_exam'].queryset = Exam.objects.filter(status=Exam.Status.PUBLISHED, original_exam__isnull=True, course__in=courses).select_related('course')

        faculty_qs = FacultyProfile.objects.select_related('user', 'department')
        self.fields['internal_examiner'].queryset = faculty_qs
        self.fields['external_examiner'].queryset = faculty_qs

        self.fields['cat_max_marks'].required = False
        self.fields['cat_max_marks'].initial = 30
        self.fields['exam_max_marks'].required = False
        self.fields['exam_max_marks'].initial = 70

        # Default internal examiner to course faculty if not set
        if not self.instance.internal_examiner_id and self.instance.course_id and self.instance.course.faculty_id:
            self.initial.setdefault('internal_examiner', self.instance.course.faculty_id)

        grade_bands = self.instance.grade_bands or default_grade_bands()
        for band in grade_bands:
            if band['grade'] != 'F':
                self.fields['grade_' + band['grade']] = forms.DecimalField(
                    min_value=0, max_value=100, decimal_places=2,
                    initial=band['minimum'], label=f"Grade {band['grade']} minimum (%)"
                )
        self.style()

    def clean(self):
        data = super().clean()
        thresholds = [(key[6:], data.get(key)) for key in self.fields if key.startswith('grade_')]
        values = [value for _, value in thresholds if value is not None]
        if len(values) == len(thresholds):
            if any(a <= b for a, b in zip(values, values[1:])) or values[-1] <= 0:
                raise forms.ValidationError('Grade thresholds must strictly decrease (e.g. A: 70, B: 60, C: 50, D: 40).')
            self.instance.grade_bands = [{'grade': grade, 'minimum': float(value)} for grade, value in thresholds] + [{'grade': 'F', 'minimum': 0}]

        from decimal import Decimal
        cat_max = data.get('cat_max_marks')
        if cat_max is None:
            cat_max = Decimal(30)
            data['cat_max_marks'] = cat_max
        exam_max = data.get('exam_max_marks')
        if exam_max is None:
            exam_max = Decimal(70)
            data['exam_max_marks'] = exam_max
        self.instance.cat_max_marks = cat_max
        self.instance.exam_max_marks = exam_max
        data['max_marks'] = int(cat_max + exam_max)
        self.instance.max_marks = int(cat_max + exam_max)
        self.instance.max_marks = int(cat_max + exam_max)

        self.instance.weight = 30 if data.get('kind') == Exam.Kind.CAT else 70
        original = data.get('original_exam')
        if original:
            data['weight'] = original.weight
            self.instance.weight = original.weight
        return data


class GradingScaleForm(StyledForm, forms.ModelForm):
    class Meta:
        model = GradingScale
        fields = ['grade', 'min_mark', 'max_mark', 'grade_point', 'description', 'order', 'is_active']
        labels = {
            'min_mark': 'Minimum Mark (%)',
            'max_mark': 'Maximum Mark (%)',
            'grade_point': 'Grade Point (e.g. 4.0)',
            'description': 'Descriptor (e.g. Excellent)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()

    def clean(self):
        data = super().clean()
        min_m = data.get('min_mark')
        max_m = data.get('max_mark')
        if min_m is not None and max_m is not None and min_m > max_m:
            raise forms.ValidationError('Minimum mark must be less than or equal to maximum mark.')
        return data


class RoomForm(StyledForm, forms.ModelForm):
    class Meta:
        model = ExamRoom
        fields = ['name', 'capacity', 'location', 'active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class TermForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AcademicTerm
        fields = ['name', 'start_date', 'end_date']
        widgets = {name: forms.DateInput(attrs={'type': 'date'}) for name in ['start_date', 'end_date']}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['end_date'] < data['start_date']:
            raise forms.ValidationError('End date must be on or after start date.')
        return data
