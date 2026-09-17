"""One policy evaluator for HTTP, navigation, login and background operations."""
import re
from contextlib import contextmanager
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from .models import (SystemRestriction, SystemModule, SystemSubmodule, SystemFeature,
                     Notice, MessageDelivery, ControlNotification, ControlHeartbeat,
                     AcademicYear, AcademicTerm, AuditLog)
from .permissions_services import has_user_permission

PERMISSIONS = {
    'maintenance': ['view','create','edit','schedule','activate','deactivate','bypass'],
    'lockdown': ['view','activate','deactivate','emergency','bypass'],
    'messages': ['view','create','edit','publish','unpublish','delete'],
    'health': ['view'],
}

def permitted(user, code):
    return bool(user and user.is_authenticated and user.is_active and has_user_permission(user, 'control.' + code))


def require_permission(user, code):
    if not permitted(user, code):
        raise PermissionDenied('You do not have permission for this system control.')


def family(restriction):
    return 'maintenance' if 'MAINTENANCE' in restriction.kind else 'lockdown'


def snapshot(obj):
    from django.core.serializers.json import DjangoJSONEncoder
    import json
    from django.forms.models import model_to_dict
    data = model_to_dict(obj)
    for key, value in data.items():
        if isinstance(value, list):
            data[key] = [getattr(v, 'pk', v) for v in value]
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def audit(obj, action, user=None, request=None, before=None, custom_description=None):
    from .audit_services import log_activity
    desc = custom_description or f'{action}: {obj}'
    log_activity(request=request, user=user, action=action, module=AuditLog.Module.CONFIG,
                 entity=obj.__class__.__name__, entity_id=obj.pk,
                 description=desc, previous_state=before, new_state=snapshot(obj))


def restrictions_at(now=None):
    now = now or timezone.now()
    return SystemRestriction.objects.filter(status__in=['ACTIVE','SCHEDULED'], starts_at__lte=now).filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)).prefetch_related('modules','submodules','features')


def path_matches(path, prefix):
    return bool(prefix) and (path == prefix.rstrip('/') or path.startswith(prefix.rstrip('/') + '/'))


def route_scope(path='', route='', namespace=''):
    modules, subs, features = set(), set(), set()
    for m in SystemModule.objects.all():
        if any((p in {namespace, namespace + ':'} or (not p.startswith('/') and f'{namespace}:{route}' == p)) or path_matches(path,p) for p in m.route_prefixes):
            modules.add(m.pk)
    for s in SystemSubmodule.objects.all():
        if route in s.route_names or f'{namespace}:{route}' in s.route_names or any(path_matches(path,p) for p in s.path_patterns):
            subs.add(s.pk); modules.add(s.module_id)
    for f in SystemFeature.objects.select_related('submodule'):
        if route in f.route_names or f'{namespace}:{route}' in f.route_names:
            features.add(f.pk); subs.add(f.submodule_id); modules.add(f.submodule.module_id)
    return modules, subs, features


def applies(r, user, scope, path=''):
    if r.roles and getattr(user,'role','PUBLIC') not in r.roles:
        return False
    selected = [{m.pk for m in r.modules.all()}, {m.pk for m in r.submodules.all()}, {m.pk for m in r.features.all()}]
    if any(selected):
        # Unclassified direct file access must not evade a scoped restriction.
        return path.startswith('/media/') or any(a & b for a,b in zip(selected,scope))
    return True


class ControlBlocked(PermissionDenied):
    def __init__(self, restriction=None, read_only=False):
        self.restriction = restriction
        self.read_only = read_only
        super().__init__('The system is currently in read-only mode. Changes cannot be made at this time.' if read_only else 'The system is temporarily unavailable.')


def evaluate(user=None, *, path='', route='', namespace='', write=False, login=False, session_started=None, scope=None):
    user = user or AnonymousUser()
    scope = scope if scope is not None else route_scope(path,route,namespace)
    read_only = None
    for r in restrictions_at().order_by('-kind'):
        if not applies(r,user,scope,path):
            continue
        if r.allow_bypass and permitted(user, family(r)+'.bypass'):
            continue
        existing_read_only = r.session_policy == 'READ_ONLY' and session_started and session_started < r.starts_at.timestamp() and not login
        if r.kind == 'READ_ONLY' or existing_read_only:
            read_only = r
            if not write:
                continue
        raise ControlBlocked(r, read_only=bool(r.kind == 'READ_ONLY' or existing_read_only))
    # Respect underlying module states as well as the incident overlays.
    if not permitted(user,'lockdown.bypass'):
        for model, ids in zip((SystemModule,SystemSubmodule,SystemFeature), scope):
            if model.objects.filter(pk__in=ids).exclude(status='ENABLED').exists():
                raise ControlBlocked()
    return read_only


def current_status():
    now = timezone.now()
    rows = list(restrictions_at(now))
    kinds = {r.kind for r in rows}
    status = next((label for kind,label in [('EMERGENCY','Emergency Lockdown'),('LOCKDOWN','Lockdown'),('EMERGENCY_MAINTENANCE','Emergency Maintenance'),('MAINTENANCE','Maintenance'),('MODULE','Restricted'),('ROLE','Restricted'),('READ_ONLY','Read Only')] if kind in kinds),'Operational')
    if status == 'Operational' and (SystemModule.objects.exclude(status='ENABLED').exists() or SystemSubmodule.objects.exclude(status='ENABLED').exists() or SystemFeature.objects.exclude(status='ENABLED').exists()):
        status = 'Restricted'

    upcoming = SystemRestriction.objects.filter(status='SCHEDULED', starts_at__gt=now).order_by('starts_at').first()
    upcoming_data = None
    if upcoming:
        secs = max(0, int((upcoming.starts_at - now).total_seconds()))
        upcoming_data = {
            'title': upcoming.title,
            'starts_at': upcoming.starts_at.isoformat(),
            'seconds_until': secs,
            'minutes_until': secs // 60,
            'message': upcoming.public_message or upcoming.description or "Scheduled maintenance",
        }

    active_msg = rows[0].public_message if rows and rows[0].public_message else ""

    active_maint = next((r for r in rows if 'MAINTENANCE' in r.kind), None)
    active_maint_data = None
    if active_maint:
        rem_secs = max(0, int((active_maint.ends_at - now).total_seconds())) if active_maint.ends_at else None
        starter_name = getattr(active_maint.activated_by, 'display_name', None) or (active_maint.activated_by.username if active_maint.activated_by else "System Administrator")
        active_maint_data = {
            'id': active_maint.pk,
            'title': active_maint.title,
            'reason': active_maint.reason,
            'starts_at': active_maint.starts_at.isoformat(),
            'ends_at': active_maint.ends_at.isoformat() if active_maint.ends_at else None,
            'remaining_seconds': rem_secs,
            'started_by': starter_name,
            'status': "Maintenance in progress",
            'scope': "Entire System" if not active_maint.modules.exists() and not active_maint.roles else "Custom Scope",
            'message': active_maint.public_message,
        }

    return {
        'status': status,
        'maintenance': bool(kinds & {'MAINTENANCE','EMERGENCY_MAINTENANCE'}),
        'lockdown': bool(kinds & {'MODULE','ROLE','LOCKDOWN','EMERGENCY'}),
        'read_only': 'READ_ONLY' in kinds,
        'upcoming': upcoming_data,
        'active_message': active_msg,
        'message': active_msg,
        'active_maintenance': active_maint_data,
    }


@transaction.atomic
def transition(pk, action, user=None, request=None, phrase='', notes='', automatic=False):
    r = SystemRestriction.objects.select_for_update().get(pk=pk)
    before = snapshot(r)
    if not automatic:
        perm = 'schedule' if action == 'schedule' and family(r) == 'maintenance' else 'activate' if action in {'activate','schedule'} else 'deactivate'
        require_permission(user, family(r)+'.'+perm)
        if action in {'activate','schedule'}:
            # Keep an authenticated recovery path for this operator.
            require_permission(user, family(r)+'.deactivate')
        if r.kind in {'EMERGENCY','EMERGENCY_MAINTENANCE'} and action in {'activate','schedule'}:
            require_permission(user,'lockdown.emergency')
            expected = 'ENABLE EMERGENCY MAINTENANCE' if r.kind == 'EMERGENCY_MAINTENANCE' else 'ENABLE EMERGENCY LOCKDOWN'
            if phrase != expected:
                raise ValidationError(f'Type {expected} to confirm.')
    allowed = {'schedule': {'DRAFT'}, 'activate': {'DRAFT','SCHEDULED'}, 'complete': {'ACTIVE','SCHEDULED'}, 'cancel': {'DRAFT','SCHEDULED'}}
    if action not in allowed or r.status not in allowed[action]:
        raise ValidationError('This transition is no longer available. Refresh and try again.')
    now = timezone.now()
    custom_desc = None
    log_action = action.upper()
    if action == 'schedule':
        if r.starts_at <= now or not r.ends_at:
            raise ValidationError('A schedule needs a future start and an end.')
        r.status = 'SCHEDULED'
    elif action == 'activate':
        r.status = 'ACTIVE'; r.activated_by = user; r.activated_at = now
        if not automatic:
            r.starts_at = now
        r.affected_user_count = get_user_model().objects.filter(is_active=True).filter(Q(role__in=r.roles) if r.roles else Q()).count()
        if 'MAINTENANCE' in r.kind:
            dur_mins = round((r.ends_at - r.starts_at).total_seconds() / 60) if r.ends_at else 2
            starter = getattr(user, 'display_name', None) or (user.username if user else 'System Administrator')
            custom_desc = (
                f"System Maintenance Started\n"
                f"Duration: {dur_mins} minutes\n"
                f"Started By: {starter}\n"
                f"Start Time: {r.starts_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Expected End: {r.ends_at.strftime('%Y-%m-%d %H:%M:%S') if r.ends_at else 'Indefinite'}"
            )
            log_action = 'MAINTENANCE_START'
    else:
        r.status = 'COMPLETED' if action == 'complete' else 'CANCELLED'
        r.completed_by = user; r.completed_at = now; r.completion_notes = notes
        if action == 'complete' and 'MAINTENANCE' in r.kind:
            if automatic:
                custom_desc = (
                    f"System Maintenance Automatically Completed\n"
                    f"Status: Operational\n"
                    f"Actual Resume Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                log_action = 'MAINTENANCE_AUTO_COMPLETE'
            else:
                custom_desc = (
                    f"System Maintenance Manually Completed\n"
                    f"Status: Operational\n"
                    f"Resume Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                log_action = 'MAINTENANCE_COMPLETE'
    r.full_clean(); r.save()
    audit(r, log_action, user, request, before, custom_description=custom_desc)
    notify_restriction(r, action)
    if action == 'activate' and r.session_policy == 'LOGOUT':
        terminate_sessions(r)
    return r


def terminate_sessions(r, user_ids=None):
    from django.contrib.sessions.models import Session
    User = get_user_model()
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        uid = session.get_decoded().get('_auth_user_id')
        user = User.objects.filter(pk=uid).first() if uid else None
        if not user or (user_ids is not None and user.pk not in user_ids):
            continue
        if permitted(user,family(r)+'.deactivate') or (r.allow_bypass and permitted(user,family(r)+'.bypass')):
            continue
        if not r.roles or user.role in r.roles:
            session.delete()


def notify_restriction(r, event):
    if event in {'complete', 'cancel'}:
        # When maintenance is complete or cancelled, expire all associated banners immediately
        Notice.objects.filter(restriction=r).update(status='EXPIRED')
        return

    marker, created = ControlNotification.objects.get_or_create(restriction=r, event=event)
    if not created:
        return
    body = r.notification_message or r.public_message
    if event.startswith('warning:'):
        body = f'System maintenance begins in {event.split(":")[1]} minutes. ' + body
    n = Notice.objects.create(
        title=r.title,
        body=body,
        message_type='MAINTENANCE',
        priority='HIGH',
        status='PUBLISHED',
        starts_at=timezone.now(),
        ends_at=r.ends_at,
        restriction=r,
        created_by=r.created_by,
        locations=['BANNER', 'IN_APP']
    )
    n.modules.set(r.modules.all())
    marker.message = n
    marker.save(update_fields=['message'])


def audience_matches(n,user):
    authenticated = bool(user and user.is_authenticated)
    if n.audience != 'ALL' and (not authenticated or n.audience != user.role):
        return False
    if n.restriction_id and n.restriction.roles and (not authenticated or user.role not in n.restriction.roles):
        return False
    if n.recipients.exists() and (not authenticated or not n.recipients.filter(pk=user.pk).exists()):
        return False
    if n.target_roles.exists() and (not authenticated or not n.target_roles.filter(assignments__user=user,assignments__is_active=True).exists()):
        return False
    student = getattr(user,'student_profile',None) if authenticated else None
    faculty = getattr(user,'faculty_profile',None) if authenticated else None
    program = getattr(student,'program',None)
    dept_id = getattr(program,'department_id',None) or getattr(faculty,'department_id',None)
    if n.departments.exists() and not n.departments.filter(pk=dept_id).exists():
        return False
    if n.programmes.exists() and not n.programmes.filter(pk=getattr(program,'pk',None)).exists():
        return False
    return True


def active_messages(user, location=None, module_ids=None, include_archived=False):
    now=timezone.now()
    qs=Notice.objects.filter(status__in=['PUBLISHED','SCHEDULED']).filter(Q(starts_at__isnull=True)|Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)).select_related('restriction','created_by').prefetch_related('recipients','target_roles','departments','programmes','modules')
    output=[]
    archived=set(MessageDelivery.objects.filter(recipient=user,archived_at__isnull=False).values_list('message_id',flat=True)) if user and user.is_authenticated else set()
    for n in qs:
        if not audience_matches(n,user) or (not include_archived and n.pk in archived):
            continue
        if location and n.locations and location not in n.locations:
            continue
        if module_ids is not None and n.modules.exists() and not set(n.modules.values_list('pk',flat=True)) & set(module_ids):
            continue
        n.rendered_body=render_fields(n.body,user,n.restriction)
        n.rendered_title=render_fields(n.title,user,n.restriction)
        output.append(n)
    weights={'CRITICAL':0,'HIGH':1,'NORMAL':2,'LOW':3}
    return sorted(output,key=lambda n:(weights[n.priority],n.display_order,-n.pk))


DYNAMIC_FIELDS = {'system_name','university_name','academic_year','semester','maintenance_start','maintenance_end','start_time','end_time','student_name','programme_name','registration_deadline','deadline','exam_start_date','system_status'}

def validate_fields(value):
    unknown=set(re.findall(r'{{\s*(\w+)\s*}}',value))-DYNAMIC_FIELDS
    if unknown:
        raise ValidationError('Unknown dynamic fields: '+', '.join(sorted(unknown)))


def render_fields(value,user=None,restriction=None):
    from cms.models import SiteSettings
    site=SiteSettings.objects.first()
    term=AcademicTerm.objects.filter(is_current=True).select_related('academic_year').first()
    year=term.academic_year if term else AcademicYear.objects.filter(is_current=True).first()
    student=getattr(user,'student_profile',None) if user and user.is_authenticated else None
    program=getattr(student,'program',None)
    values={'system_name':getattr(site,'site_name',''), 'university_name':getattr(site,'site_name',''),
            'academic_year':getattr(year,'name',''), 'semester':getattr(term,'name',''),
            'student_name':getattr(user,'display_name','') if user and user.is_authenticated else '',
            'programme_name':getattr(program,'name',''), 'registration_deadline':getattr(term,'registration_end_date',None),
            'exam_start_date':getattr(term,'exam_start_date',None), 'system_status':current_status()['status'],
            'maintenance_start':getattr(restriction,'starts_at',None),'maintenance_end':getattr(restriction,'ends_at',None)}
    values.update(start_time=values['maintenance_start'],end_time=values['maintenance_end'],deadline=values['registration_deadline'])
    return re.sub(r'{{\s*(\w+)\s*}}',lambda m:str(values.get(m[1]) or 'Not configured'),value)


@transaction.atomic
def tick():
    """Idempotent lifecycle reconciliation; policy evaluates dates even if scheduler is late."""
    now = timezone.now()
    for r in SystemRestriction.objects.filter(status__in=['SCHEDULED','ACTIVE']):
        try:
            if r.ends_at and r.ends_at <= now:
                transition(r.pk, 'complete', automatic=True)
            elif r.status == 'SCHEDULED' and r.starts_at <= now:
                transition(r.pk, 'activate', automatic=True)
            elif r.status == 'SCHEDULED':
                for minutes in r.warning_minutes:
                    if r.starts_at - timedelta(minutes=minutes) <= now:
                        notify_restriction(r, f'warning:{minutes}')
        except Exception as error:
            if r.status == 'ACTIVE' and r.ends_at and r.ends_at <= now:
                from .audit_services import log_activity
                log_activity(
                    user=None, action='MAINTENANCE_AUTO_EXPIRY_FAILURE', module=AuditLog.Module.CONFIG,
                    entity=r.__class__.__name__, entity_id=r.pk,
                    description=f"Maintenance Auto-Expiry Failure for {r.title} (ID {r.pk}): {error}"
                )
                try:
                    Notice.objects.create(
                        title="Maintenance Auto-Expiry Failure",
                        body=f"Critical system alert: Maintenance '{r.title}' failed to auto-terminate: {error}",
                        message_type='EMERGENCY', priority='CRITICAL', status='PUBLISHED',
                        starts_at=now, locations=['BANNER', 'IN_APP']
                    )
                except Exception:
                    pass

    for n in Notice.objects.select_for_update().filter(status__in=['SCHEDULED','PUBLISHED']):
        old=n.status
        if n.ends_at and n.ends_at<=now:
            n.status='EXPIRED'
            n.deliveries.filter(read_at__isnull=True).update(status='EXPIRED')
        elif n.status=='SCHEDULED' and (not n.starts_at or n.starts_at<=now):
            n.status='PUBLISHED'
        if old!=n.status:
            n.save(update_fields=['status','updated_at']); audit(n,n.status,before={'status':old})
    ControlHeartbeat.objects.update_or_create(key='scheduler',defaults={'last_success_at':now})
    try:
        from .backup_services import backup_tick
        backup_tick()
    except Exception:
        pass
    try:
        from .audit_services import apply_audit_retention
        apply_audit_retention()
    except Exception:
        pass
    try:
        from .webhook_services import deliver_pending_webhooks
        deliver_pending_webhooks()
    except Exception:
        pass


@transaction.atomic
def start_maintenance_mode(duration_minutes=2, user=None, reason=None, public_message=None, notification_message=None):
    """
    Authoritative server-level activation of system-wide maintenance mode for specified duration (default: 2 minutes).
    Enforces server-authoritative timestamps and sets up automatic resume.
    """
    now = timezone.now()
    ends_at = now + timedelta(minutes=duration_minutes)

    # Complete any currently active conflicting maintenance
    for active_r in SystemRestriction.objects.filter(status='ACTIVE', kind=SystemRestriction.Kind.MAINTENANCE):
        transition(active_r.pk, 'complete', user=user, notes="Superseded by new maintenance window", automatic=True)

    default_msg = "We're performing a short system maintenance operation to improve system reliability and performance. Please wait a moment while we complete the process."
    default_notice = "🔧 SYSTEM MAINTENANCE — UMS will temporarily be unavailable while maintenance is in progress. Normal operations will automatically resume shortly."
    default_reason = f"System maintenance operation to improve system reliability and performance ({duration_minutes} minutes window)."

    r = SystemRestriction.objects.create(
        title="System Under Maintenance",
        description=f"Controlled {duration_minutes}-minute system maintenance window.",
        kind=SystemRestriction.Kind.MAINTENANCE,
        reason=reason or default_reason,
        public_message=public_message or default_msg,
        notification_message=notification_message or default_notice,
        status=SystemRestriction.Status.DRAFT,
        starts_at=now,
        ends_at=ends_at,
        allow_bypass=True,
        session_policy='BLOCK',
        created_by=user,
    )
    # Activate with server authoritative time
    r = transition(r.pk, 'activate', user=user, automatic=True)
    return r


def deliver_messages():
    """Reuse Notice records; the recipient ledger is unique across retries."""
    from django.core.mail import send_mail
    from django.conf import settings
    for user in get_user_model().objects.filter(is_active=True).iterator():
        for n in active_messages(user, include_archived=True):
            methods=['IN_APP']
            if 'EMAIL' in n.locations and user.email:
                methods.append('EMAIL')
            for method in methods:
                with transaction.atomic():
                    d,_=MessageDelivery.objects.get_or_create(message=n,recipient=user,method=method)
                    d=MessageDelivery.objects.select_for_update().get(pk=d.pk)
                    if d.status not in {'PENDING','FAILED'}:
                        continue
                    if method=='EMAIL':
                        try:
                            send_mail(n.rendered_title,n.rendered_body,settings.DEFAULT_FROM_EMAIL,[user.email],fail_silently=False)
                        except Exception:
                            d.status='FAILED'; d.save(update_fields=['status']); continue
                    d.status='SENT' if method=='EMAIL' else 'DELIVERED'
                    d.sent_at=timezone.now()
                    if method=='IN_APP': d.delivered_at=d.sent_at
                    d.save()


@contextmanager
def controlled_operation(user=None, *, module=None, write=True):
    """Required entry point for non-HTTP jobs which access business data."""
    scope=({module.pk},set(),set()) if module else (set(),set(),set())
    evaluate(user,write=write,scope=scope)
    yield
