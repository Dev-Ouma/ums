from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.utils import timezone

from university.models import Program

from .models import FacultyProfile, Role, StudentProfile, User

INPUT = "form-control form-control-lg"
FIELD = "form-control"
SELECT = "form-select"


# Shown only to someone who already proved they know the password, so the
# message cannot be used to enumerate or probe accounts.
BLOCKED_ACCOUNT_MESSAGES = {
    "LOCKED": "This account is temporarily locked after repeated failed sign-in "
              "attempts. Try again later or contact the ICT service desk.",
    "SUSPENDED": "This account is suspended. Contact the ICT service desk.",
    "DISABLED": "This account has been disabled and can no longer sign in.",
    "EXPIRED": "This account has expired. Contact the ICT service desk to renew it.",
    "PENDING": "This account has not been activated yet. Use the activation link "
               "sent to your email, or request a new one below.",
    "INACTIVE": "This account is not active. Contact the ICT service desk.",
    "ARCHIVED": "This account has been archived and can no longer sign in.",
}


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Invalid username or password.",
    }
    username = forms.CharField(widget=forms.TextInput(
        attrs={"class": INPUT, "placeholder": "Username", "autofocus": True,
               "autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={"class": INPUT, "placeholder": "Password", "autocomplete": "current-password"}))
    remember_me = forms.BooleanField(
        label="Remember me", required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))

    def clean(self):
        """
        Explain a blocked account instead of reporting bad credentials.

        The status is only disclosed once the supplied password checks out, so
        an attacker without the password still learns nothing.
        """
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            from university.identity_models import UserAccount
            user = User.objects.filter(username__iexact=username).first()
            if user is not None and user.check_password(password):
                account = UserAccount.objects.filter(user=user).first()
                if account is not None and not account.can_authenticate:
                    raise forms.ValidationError(
                        BLOCKED_ACCOUNT_MESSAGES.get(
                            account.effective_status,
                            "This account cannot sign in at the moment."),
                        code="account_blocked")
        return super().clean()


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
        from university.identity_models import AccountStatus, UserType
        from university.identity_services import create_user_account, provision_student_account

        d = self.cleaned_data
        if not commit:
            # Dry-run not supported via identity service; create unsaved instance
            user = super().save(commit=False)
            user.role = Role.STUDENT
            user.set_password(d["password1"])
            return user

        # Route through the central identity service so every self-registered
        # student gets a proper UserAccount envelope, audit log entry, and
        # institutional email — matching the admin-created workflow exactly.
        created = create_user_account(
            user_type=UserType.STUDENT,
            first_name=d["first_name"],
            last_name=d["last_name"],
            email=d["email"],
            username=d["username"],
            phone="",
            role=Role.STUDENT,
            password_mode="MANUAL",
            password=d["password1"],
            status=AccountStatus.ACTIVE,
            must_change_password=False,
            actor=None,
            notify=False,
        )
        user = created["user"]

        count = StudentProfile.objects.count() + 1
        sp, _ = StudentProfile.objects.get_or_create(
            user=user,
            defaults={
                "roll_no": f"UMS{timezone.now().year}{count:04d}",
                "program": d.get("program"),
                "admission_date": timezone.now().date(),
            },
        )

        # Complete the identity envelope (UserAccount status, email, etc.)
        provision_student_account(sp, actor=None, notify=False)
        user.refresh_from_db()

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
        fields = [
            "gender", "date_of_birth", "address", "guardian_name",
            "guardian_relationship", "guardian_phone", "guardian_email", "guardian_address",
        ]
        widgets = {
            "gender": forms.Select(attrs={"class": SELECT}),
            "date_of_birth": forms.DateInput(attrs={"class": FIELD, "type": "date"}),
            "address": forms.TextInput(attrs={"class": FIELD, "placeholder": "Postal or residential address"}),
            "guardian_name": forms.TextInput(attrs={"class": FIELD, "placeholder": "Guardian / next of kin"}),
            "guardian_relationship": forms.TextInput(attrs={"class": FIELD, "placeholder": "Parent, guardian, sponsor, etc."}),
            "guardian_phone": forms.TextInput(attrs={"class": FIELD, "placeholder": "+254 700 000 000"}),
            "guardian_email": forms.EmailInput(attrs={"class": FIELD, "placeholder": "guardian@example.com"}),
            "guardian_address": forms.TextInput(attrs={"class": FIELD, "placeholder": "Guardian postal or residential address"}),
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

    def clean_new_password1(self):
        """Apply the institution's configured password policy, not just Django's."""
        from university.identity_services import validate_password
        password = self.cleaned_data["new_password1"]
        errors = validate_password(password, user=self.user)
        if errors:
            raise forms.ValidationError(errors)
        return password


# ==============================================================================
# CENTRAL IDENTITY — SELF-SERVICE CREDENTIAL FORMS
# ==============================================================================

class PasswordResetRequestForm(forms.Form):
    """
    Step one of self-service recovery.

    Accepts either a username or an email address and never confirms which of
    them exists — account enumeration is prevented by the view always reporting
    the same outcome.
    """
    identifier = forms.CharField(
        label="Username or email",
        max_length=254,
        widget=forms.TextInput(attrs={
            "class": INPUT, "placeholder": "Username or institutional email",
            "autofocus": True, "autocomplete": "username",
        }),
    )


class IdentitySetPasswordForm(forms.Form):
    """
    Choose a new password, validated against the institution's password policy.

    The policy lives in ``identity_services`` so this form, the administrator
    tools and the bulk operations all enforce exactly the same rules.
    """
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={
            "class": INPUT, "placeholder": "New password", "autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={
            "class": INPUT, "placeholder": "Repeat new password", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password1(self):
        from university.identity_services import validate_password
        password = self.cleaned_data["new_password1"]
        errors = validate_password(password, user=self.user)
        if errors:
            raise forms.ValidationError(errors)
        return password

    def clean(self):
        cleaned = super().clean()
        first, second = cleaned.get("new_password1"), cleaned.get("new_password2")
        if first and second and first != second:
            self.add_error("new_password2", "The two passwords do not match.")
        return cleaned


class UserSignatureForm(forms.ModelForm):
    """
    Form for uploading or updating official user signature image,
    title, and department/office.
    """
    signature_file = forms.FileField(
        required=False,
        label="Signature Image",
        widget=forms.FileInput(attrs={
            "class": "form-control",
            "accept": "image/png,image/jpeg,image/webp",
            "id": "id_signature_file"
        })
    )
    change_reason = forms.CharField(
        required=False,
        label="Reason for Update / Replacement",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. New appointment / refreshed official digital signature"
        })
    )

    class Meta:
        from .models import UserSignature
        model = UserSignature
        fields = ["title", "department_or_office", "status"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Registrar (Academic Affairs)"}),
            "department_or_office": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Office of the Registrar"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_signature_file(self):
        file = self.cleaned_data.get("signature_file")
        if file:
            from .signature_services import validate_signature_file
            validate_signature_file(file)
        return file

