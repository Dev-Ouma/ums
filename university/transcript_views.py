from university.document_views import present_pdf
"""Private transcript portal and PDF delivery."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.db.models import Q

from accounts.models import StudentProfile
from cms.models import SiteSettings
from django.utils import timezone
from .examination_services import is_admin
from .models import AcademicTerm, Exam, AuditLog, DocumentReleaseControl
from .transcript_io import build_transcript_context, export_transcript_pdf
from .document_access_services import check_document_access
from .audit_services import get_client_ip, detect_device_type

TITLES = {'provisional': 'Provisional Transcript', 'academic': 'Academic Transcript',
          'performance': 'Academic Performance Report'}


def student_for(request, student_id):
    students = StudentProfile.objects.select_related('user', 'program__department')
    if student_id is not None:
        if not is_admin(request.user) and not students.filter(pk=student_id, user=request.user).exists():
            raise PermissionDenied
        return get_object_or_404(students, pk=student_id)
    return get_object_or_404(students, user=request.user)


def selection(request, student):
    terms = AcademicTerm.objects.filter(exam__results__student=student,
        exam__status=Exam.Status.PUBLISHED).distinct().order_by('-start_date', '-pk')
    raw = request.GET.get('term')
    if raw:
        if not raw.isdigit():
            raise Http404('Invalid academic term')
        selected = get_object_or_404(terms, pk=raw)
    else:
        selected = terms.first()
    return terms, selected


@login_required
@never_cache
def portal(request, student_id=None):
    if student_id is None and is_admin(request.user):
        from django.core.paginator import Paginator
        query = request.GET.get('q', '').strip()
        students = StudentProfile.objects.select_related('user', 'program').filter(
            Q(roll_no__icontains=query) | Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) | Q(user__username__icontains=query))
        return render(request, 'transcripts/students.html', {'students': Paginator(students,25).get_page(request.GET.get('page')), 'q':query})
    student = student_for(request, student_id)
    terms, selected = selection(request,student)
    ctx = build_transcript_context(student)
    maximum = max([4] + [float(s['term_gpa']) for s in ctx['semesters'] if s['term_gpa'] is not None])
    count = len(ctx['semesters'])
    trends = []
    for i, sem in enumerate(ctx['semesters']):
        if sem['term_gpa'] is None or sem['cumulative_gpa'] is None:
            continue
        x = 60 + i * 600 / max(count-1,1)
        trends.append(dict(x=round(x,2),gpa=round(180-float(sem['term_gpa'] or 0)/maximum*150,2),
                           cgpa=round(180-float(sem['cumulative_gpa'] or 0)/maximum*150,2),semester=sem))
    ctx.update(terms=terms,selected_term=selected, trends=trends, chart_max=maximum,
               gpa_points=' '.join(f"{p['x']},{p['gpa']}" for p in trends),
               cgpa_points=' '.join(f"{p['x']},{p['cgpa']}" for p in trends))
    return render(request,'transcripts/portal.html',ctx)


@login_required
@never_cache
@xframe_options_sameorigin
def document(request, student_id, kind):
    if kind not in TITLES:
        raise Http404
    student = student_for(request, student_id)
    terms, selected = selection(request,student) if kind == 'provisional' else (None,None)

    # Access control & release window check
    doc_type_map = {
        'provisional': DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL,
        'academic': DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL,
        'performance': DocumentReleaseControl.DocumentType.PROGRESS_REPORT,
    }
    doc_type = doc_type_map.get(kind, DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL)
    allowed, reason, control, is_bypass = check_document_access(student, doc_type, term=selected, user=request.user)
    if not allowed:
        return render(request, 'documents/locked.html', {
            'student': student,
            'title': f"{TITLES[kind]} Unavailable",
            'reason': reason,
            'control': control,
        }, status=403)

    ctx = build_transcript_context(student, selected)
    if request.GET.get('format') != 'pdf':
        pdf_url = reverse('examinations:transcript_document',args=[student.pk,kind])+'?format=pdf'
        if selected:
            pdf_url += f'&term={selected.pk}'
        return render(request,'transcripts/viewer.html',dict(student=student,title=TITLES[kind],pdf_url=pdf_url))

    branding = SiteSettings.objects.first()
    logo = Path(settings.BASE_DIR)/'static/img/ums-logo.png'
    if branding and branding.logo:
        try:
            logo = branding.logo.path
        except NotImplementedError:
            logo = None

    client_ip = get_client_ip(request)
    tracking_info = f"Generated for {request.user.username} · IP: {client_ip} · {timezone.now().strftime('%d %b %Y %H:%M')}"
    verify_url = request.build_absolute_uri(reverse('university:verify_document', kwargs={'reference_no': ctx['reference_no']}))

    data = export_transcript_pdf(
        student,
        ctx,
        kind,
        site_name=branding.site_name if branding else 'University Management System',
        site_address=branding.contact_address if branding else '',
        site_email=branding.contact_email if branding else '',
        site_phone=branding.contact_phone if branding else '',
        logo_path=str(logo) if logo and Path(logo).is_file() else None,
        verify_url=verify_url,
        tracking_info=tracking_info
    )

    try:
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.EXPORT,
            module=AuditLog.Module.ACADEMICS,
            ip_address=client_ip,
            device=detect_device_type(request.META.get("HTTP_USER_AGENT", "")),
            details=f"Exported {TITLES[kind]} for {student.roll_no} (Ref: {ctx['reference_no']}) - Bypass: {is_bypass}"
        )
    except Exception:
        pass

    response = HttpResponse(data, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{kind}-transcript-{student.pk}.pdf"'
    response['X-Content-Type-Options'] = 'nosniff'
    return present_pdf(request, response)
