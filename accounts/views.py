from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (AvatarForm, FacultyDetailsForm, LoginForm, ProfileForm,
                    SignUpForm, StudentDetailsForm, UMSPasswordChangeForm)


class UMSLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.session.pop('_control_recovery_login', False):
            from django.urls import reverse
            return reverse('control:dashboard')
        return super().get_success_url()

    def form_valid(self, form):
        from university.control_services import evaluate, ControlBlocked, permitted
        from university.control_middleware import blocked_response
        from django.utils import timezone
        user = form.get_user()
        recovery = any(permitted(user, p) for p in ['maintenance.deactivate', 'lockdown.deactivate'])
        if not recovery:
            try:
                evaluate(user, login=True)
            except ControlBlocked as error:
                return blocked_response(self.request, error)
        self.request.session['_control_login_at'] = timezone.now().timestamp()
        if recovery:
            self.request.session['_control_recovery_login'] = True
        messages.success(self.request, f"Welcome back, {form.get_user().display_name}!")
        return super().form_valid(form)


@require_POST
def logout_view(request):
    """Sign the user out.

    POST-only and CSRF-protected: a GET logout can be triggered by any page
    that embeds a link to it (or by a prefetching browser), so every logout
    control in the UI submits a form instead. ``logout()`` flushes the session,
    which cycles the session key and drops the auth hash, so the old session
    cookie cannot be replayed against protected pages.
    """
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("university:home")


def signup(request):
    if request.user.is_authenticated:
        return redirect("university:dashboard")
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your student account is ready. Welcome to UMS!")
            return redirect("university:dashboard")
    else:
        form = SignUpForm()
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def profile(request):
    return render(request, "accounts/profile.html")


def _details_form_for(user, data=None):
    """Return the role-specific profile form bound to this user, if any.

    Each role only ever gets the form for its own profile record, which is how
    field-level authorisation is enforced: an admin has no student form, and a
    student's form simply does not contain roll_no or program.
    """
    if user.is_student and hasattr(user, "student_profile"):
        return StudentDetailsForm(data, instance=user.student_profile, prefix="details")
    if user.is_faculty and hasattr(user, "faculty_profile"):
        return FacultyDetailsForm(data, instance=user.faculty_profile, prefix="details")
    return None


@login_required
def profile_settings(request):
    """View and update the signed-in user's own profile.

    Three independent forms share one page; the submitted ``form_type`` decides
    which one is bound, so a validation error in one section never discards
    what the user typed elsewhere.
    """
    user = request.user
    form_type = request.POST.get("form_type") if request.method == "POST" else None

    profile_form = ProfileForm(instance=user, prefix="profile")
    details_form = _details_form_for(user)
    avatar_form = AvatarForm(instance=user, prefix="avatar")
    password_form = UMSPasswordChangeForm(user=user, prefix="password")
    # ?tab= lets the header menu deep-link straight to a section.
    requested = request.GET.get("tab")
    active_tab = requested if requested in {"profile", "avatar", "password", "activity"} else "profile"

    if form_type == "profile":
        profile_form = ProfileForm(request.POST, instance=user, prefix="profile")
        details_form = _details_form_for(user, request.POST)
        forms_ok = profile_form.is_valid()
        if details_form is not None:
            forms_ok = details_form.is_valid() and forms_ok
        if forms_ok:
            profile_form.save()
            if details_form is not None:
                details_form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile_settings")
        messages.error(request, "Please correct the highlighted fields and try again.")

    elif form_type == "avatar":
        active_tab = "avatar"
        avatar_form = AvatarForm(request.POST, request.FILES, instance=user, prefix="avatar")
        if avatar_form.is_valid():
            avatar_form.save()
            messages.success(request, "Your profile picture has been updated.")
            return redirect("accounts:profile_settings")
        messages.error(request, "That image could not be saved — see the message below.")

    elif form_type == "password":
        active_tab = "password"
        password_form = UMSPasswordChangeForm(user=user, data=request.POST, prefix="password")
        if password_form.is_valid():
            # Routed through the central identity service so password history,
            # expiry stamps, token invalidation and session revocation all apply.
            from university.identity_services import record_password_change
            record_password_change(
                user, password_form.cleaned_data["new_password1"], actor=user,
                request=request, keep_session_key=request.session.session_key,
                reason="Self-service change from profile settings")
            # Changing the password rotates the auth hash, which would sign the
            # user out of this session too; re-stamp it so they stay signed in.
            update_session_auth_hash(request, password_form.user)
            messages.success(request, "Your password has been changed.")
            return redirect("accounts:profile_settings")
        messages.error(request, "Your password could not be changed — see the errors below.")

    return render(request, "accounts/profile_settings.html", {
        "profile_form": profile_form,
        "details_form": details_form,
        "avatar_form": avatar_form,
        "password_form": password_form,
        "active_tab": active_tab,
    })


# ==============================================================================
# SELF-SERVICE CREDENTIAL RECOVERY
# ==============================================================================

def password_reset_request(request):
    """
    Start a self-service reset.

    The response is identical whether or not the account exists, so this page
    cannot be used to discover valid usernames or email addresses.
    """
    from django.db.models import Q

    from university.identity_models import PasswordResetToken
    from university.identity_services import (build_reset_url, get_password_policy,
                                              issue_reset_token)
    from university.email_services import notify_password_reset
    from .forms import PasswordResetRequestForm
    from .models import User

    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"].strip()
        user = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
            | Q(institutional_emails__address__iexact=identifier)
        ).distinct().first()

        if user is not None and user.email:
            raw_token, _record = issue_reset_token(
                user, purpose=PasswordResetToken.Purpose.RESET, request=request)
            notify_password_reset(user, build_reset_url(raw_token, request),
                                  get_password_policy()["reset_token_minutes"],
                                  request=request)
        return redirect("accounts:password_reset_done")

    return render(request, "accounts/password_reset_request.html", {"form": form})


def password_reset_done(request):
    return render(request, "accounts/password_reset_done.html")


def password_reset_confirm(request, token):
    """
    Finish a reset or a first-time activation.

    The token is validated on GET as well as POST so an expired link shows a
    clear message rather than a form that will fail on submit.
    """
    from university.identity_services import (consume_reset_token, get_password_policy,
                                              get_valid_token)
    from .forms import IdentitySetPasswordForm

    record = get_valid_token(token)
    if record is None:
        return render(request, "accounts/password_reset_invalid.html", status=400)

    form = IdentitySetPasswordForm(request.POST or None, user=record.user)
    if request.method == "POST" and form.is_valid():
        ok, message, _user = consume_reset_token(
            token, form.cleaned_data["new_password1"], request=request)
        if ok:
            messages.success(request, message)
            return redirect("accounts:login")
        messages.error(request, message)

    return render(request, "accounts/password_reset_confirm.html", {
        "form": form,
        "token_record": record,
        "policy": get_password_policy(),
        "is_activation": record.purpose != record.Purpose.RESET,
    })


@login_required
def password_change_required(request):
    """
    Mandatory password change gate.

    Reached whenever an account is flagged ``must_change_password`` or its
    password has expired; the middleware keeps redirecting here until it is done.
    """
    from university.identity_services import (get_account, get_password_policy,
                                              record_password_change)
    from .forms import IdentitySetPasswordForm

    account = get_account(request.user)
    form = IdentitySetPasswordForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        record_password_change(
            request.user, form.cleaned_data["new_password1"], actor=request.user,
            request=request, must_change=False,
            keep_session_key=request.session.session_key,
            reason="Mandatory password change")
        update_session_auth_hash(request, request.user)
        messages.success(request, "Your password has been updated. Thank you.")
        return redirect("university:dashboard")

    return render(request, "accounts/password_change_required.html", {
        "form": form,
        "account": account,
        "policy": get_password_policy(),
        "expired": bool(account and account.password_expires_at
                        and account.password_expires_at <= timezone.now()),
    })
