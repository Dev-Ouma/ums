from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.utils import timezone

from university.models import Program

from .models import FacultyProfile, Role, StudentProfile, User

INPUT = "form-control form-control-lg"
FIELD = "form-control"
SELECT = "form-select"


class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(
        attrs={"class": INPUT, "placeholder": "Username", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={"class": INPUT, "placeholder": "Password"}))


class SignUpForm(forms.ModelForm):
    """Public self-registration -> always creates a STUDENT account."""
    first_name = forms.CharField(widget=forms.TextInput(
        attrs={"class": INPUT, "placeholder": "First name"}))
    last_name = forms.CharField(widget=forms.TextInput(
        attrs={"class": INPUT, "placeholder": "Last name"}))
    email = forms.EmailField(widget=forms.EmailInput(
        attrs={"class": INPUT, "placeholder": "you@example.com"}))
    program = forms.ModelChoiceField(
        queryset=Program.objects.all(), required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-lg"}))
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(
        attrs={"class": INPUT, "placeholder": "Create a password"}))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(
        attrs={"class": INPUT, "placeholder": "Repeat password"}))

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]
        widgets = {
            "username": forms.TextInput(attrs={"class": INPUT, "placeholder": "Choose a username"}),
        }

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.STUDENT
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            count = StudentProfile.objects.count() + 1
            StudentProfile.objects.create(
                user=user,
                roll_no=f"UMS{timezone.now().year}{count:04d}",
                program=self.cleaned_data.get("program"),
                admission_date=timezone.now().date(),
            )
        return user


# ---------------------------------------------------------------------------
# Profile settings
# ---------------------------------------------------------------------------
class ProfileForm(forms.ModelForm):
    """Account details every authenticated user may edit about themselves.

    Deliberately excludes ``username``, ``role`` and the registry-controlled
    profile fields — those are administrative data, not self-service ones.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": FIELD, "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": FIELD, "placeholder": "Last name"}),
            "email": forms.EmailInput(attrs={"class": FIELD, "placeholder": "you@example.com"}),
            "phone": forms.TextInput(attrs={"class": FIELD, "placeholder": "+254 700 000 000"}),
        }

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            raise forms.ValidationError("An email address is required.")
        clash = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("That email address is already in use by another account.")
        return email


class AvatarForm(forms.ModelForm):
    """Profile picture upload, with type and size validation."""

    MAX_BYTES = 2 * 1024 * 1024  # 2 MB
    ALLOWED = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    class Meta:
        model = User
        fields = ["avatar_image"]
        widgets = {
            "avatar_image": forms.ClearableFileInput(
                attrs={"class": FIELD, "accept": "image/png,image/jpeg,image/gif,image/webp"}),
        }
        labels = {"avatar_image": "Profile picture"}

    def clean_avatar_image(self):
        image = self.cleaned_data.get("avatar_image")
        # Unchanged (already-stored FieldFile) or cleared -> nothing to check.
        if not image or not hasattr(image, "content_type"):
            return image
        if image.size > self.MAX_BYTES:
            raise forms.ValidationError("Image is too large — please keep it under 2 MB.")
        if image.content_type not in self.ALLOWED:
            raise forms.ValidationError("Unsupported file type. Use JPEG, PNG, GIF or WebP.")
        return image


class StudentDetailsForm(forms.ModelForm):
    """Student-editable slice of StudentProfile.

    roll_no, program and current_semester are intentionally absent: those are
    set by the registry and a student must not be able to rewrite them.
    """

    class Meta:
        model = StudentProfile
        fields = ["gender", "date_of_birth", "address", "guardian_name"]
        widgets = {
            "gender": forms.Select(attrs={"class": SELECT}),
            "date_of_birth": forms.DateInput(attrs={"class": FIELD, "type": "date"}),
            "address": forms.TextInput(attrs={"class": FIELD, "placeholder": "Postal or residential address"}),
            "guardian_name": forms.TextInput(attrs={"class": FIELD, "placeholder": "Guardian / next of kin"}),
        }

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > timezone.now().date():
            raise forms.ValidationError("Date of birth cannot be in the future.")
        return dob


class FacultyDetailsForm(forms.ModelForm):
    """Faculty-editable slice of FacultyProfile.

    employee_id, department, designation and joining_date stay read-only — they
    are HR records, not self-service fields.
    """

    class Meta:
        model = FacultyProfile
        fields = ["specialization"]
        widgets = {
            "specialization": forms.TextInput(
                attrs={"class": FIELD, "placeholder": "e.g. Distributed Systems"}),
        }


class UMSPasswordChangeForm(PasswordChangeForm):
    """Django's password change form, restyled for the UMS design system."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "old_password": "Current password",
            "new_password1": "New password",
            "new_password2": "Confirm new password",
        }
        for name, field in self.fields.items():
            field.widget.attrs.update({
                "class": FIELD,
                "placeholder": placeholders.get(name, ""),
                "autocomplete": "new-password",
            })
