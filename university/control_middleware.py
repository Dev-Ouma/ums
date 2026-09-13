from contextlib import ExitStack
import re
from django.contrib.auth import logout
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import resolve, Resolver404
from .control_services import ControlBlocked, evaluate, permitted, render_fields


def blocked_response(request, error):
    from django.utils import timezone
    r = error.restriction
    now = timezone.now()
    text = str(error) if error.read_only or not r else render_fields(r.public_message, request.user, r)
    remaining_seconds = max(0, int((r.ends_at - now).total_seconds())) if (r and r.ends_at) else None

    is_maintenance = bool(r and 'MAINTENANCE' in r.kind)
    status_code = 423 if error.read_only else 503

    data = {
        'status': 'READ_ONLY' if error.read_only else 'UNAVAILABLE',
        'system_status': 'Maintenance in progress' if is_maintenance else ('Read-only mode' if error.read_only else 'System Unavailable'),
        'maintenance': is_maintenance,
        'message': text,
        'maintenance_start': r.starts_at.isoformat() if (r and r.starts_at) else None,
        'expected_end': r.ends_at.isoformat() if (r and r.ends_at) else None,
        'expected_restoration': r.ends_at.isoformat() if (r and r.ends_at) else None,
        'remaining_seconds': remaining_seconds,
        'server_time': now.isoformat(),
    }

    is_json = (
        'application/json' in request.headers.get('Accept', '') or
        '/api/' in request.path or
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
        request.path.startswith('/api/')
    )

    if is_json:
        response = JsonResponse(data, status=status_code)
    else:
        ctx = {
            'availability': data,
            'restoration': r.ends_at if r else None,
            'restriction': r,
            'remaining_seconds': remaining_seconds,
            'now': now,
        }
        response = render(request, 'control/unavailable.html', ctx, status=status_code)

    response['Cache-Control'] = 'no-store, private'
    response['Retry-After'] = str(min(60, remaining_seconds)) if remaining_seconds else '60'
    return response


class SystemControlMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.utils import timezone
        from .models import ControlHeartbeat, SystemRestriction
        from .control_services import tick

        now = timezone.now()
        # Authoritative backend check: if any maintenance expired or scheduled starts, tick immediately
        if (SystemRestriction.objects.filter(status='ACTIVE', ends_at__isnull=False, ends_at__lte=now).exists() or
                SystemRestriction.objects.filter(status='SCHEDULED', starts_at__lte=now).exists()):
            tick()
        else:
            heartbeat = ControlHeartbeat.objects.filter(key='scheduler').first()
            if not heartbeat or (now - heartbeat.last_success_at).total_seconds() >= 30:
                tick()

        try:
            match = resolve(request.path_info)
            route, namespace = match.url_name, match.namespace
        except Resolver404:
            route, namespace = '', ''

        # Narrow recovery surface, still subject to per-action authorization and CSRF.
        recovery = namespace == 'control' and any(permitted(request.user, p) for p in ['maintenance.deactivate', 'lockdown.deactivate'])

        payment_callbacks = {
            'mpesa_callback', 'mpesa_validation', 'mpesa_callback_alt', 'mpesa_validation_alt',
            'card_callback', 'bank_callback', 'public_status'
        }
        exempt = (
            (namespace == 'accounts' and route in {'login', 'logout'}) or
            (namespace == 'control' and route == 'status') or
            route in payment_callbacks or
            request.path_info.startswith('/status/') or
            request.path_info.startswith('/api/payments/callback/') or
            request.path_info.startswith('/finance/pay/callback/')
        )

        if recovery or exempt:
            if recovery:
                active_maint = SystemRestriction.objects.filter(status='ACTIVE', kind=SystemRestriction.Kind.MAINTENANCE).first()
                if active_maint and request.user.is_authenticated:
                    from .audit_services import log_activity
                    from .models import AuditLog
                    log_activity(
                        request=request, user=request.user, action='MAINTENANCE_BYPASS', module=AuditLog.Module.AUTH,
                        entity='SystemRestriction', entity_id=active_maint.pk,
                        description=f"Authorized maintenance bypass access to {request.path_info} by {request.user.username}"
                    )
            response = self.get_response(request)
            response['Cache-Control'] = 'no-store, private'
            return response

        try:
            read_only = evaluate(
                request.user,
                path=request.path_info,
                route=route,
                namespace=namespace,
                write=request.method not in {'GET', 'HEAD', 'OPTIONS'},
                session_started=request.session.get('_control_login_at') or (
                    request.user.last_login.timestamp() if request.user.is_authenticated and request.user.last_login else None
                )
            )

            # If user has maintenance bypass, record audit log entry
            active_maint = SystemRestriction.objects.filter(status='ACTIVE', kind=SystemRestriction.Kind.MAINTENANCE).first()
            if active_maint and request.user.is_authenticated and (permitted(request.user, 'maintenance.bypass') or request.user.is_superuser):
                if not request.path_info.startswith(('/static/', '/media/', '/favicon.ico')):
                    from .audit_services import log_activity
                    from .models import AuditLog
                    log_activity(
                        request=request, user=request.user, action='MAINTENANCE_BYPASS', module=AuditLog.Module.AUTH,
                        entity='SystemRestriction', entity_id=active_maint.pk,
                        description=f"Authorized maintenance bypass access to {request.path_info} by {request.user.username}"
                    )

        except ControlBlocked as error:
            r = error.restriction
            if not r:
                return self.get_response(request)
            if r and r.session_policy == 'LOGOUT' and request.user.is_authenticated:
                if not any(permitted(request.user, p) for p in ['maintenance.deactivate', 'lockdown.deactivate']):
                    logout(request)
            return blocked_response(request, error)
        request.control_read_only=read_only
        with ExitStack() as stack:
            if read_only:
                # Catch legacy GET views which write business rows as a side effect.
                for conn in connections.all():
                    stack.enter_context(conn.execute_wrapper(self.write_guard))
            response=self.get_response(request)
        if read_only:
            response['Cache-Control']='no-store, private'
        return response

    @staticmethod
    def write_guard(execute,sql,params,many,context):
        operation=sql.lstrip().split(None,1)[0].upper() if sql.strip() else ''
        if operation in {'INSERT','UPDATE','DELETE','REPLACE','CREATE','ALTER','DROP','TRUNCATE','WITH'}:
            # Session, audit telemetry and default site settings remain available; application data is immutable.
            match=re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+["`]?([\w]+)',sql,re.I)
            if not match or match[1] not in {'django_session','university_auditlog','university_messagedelivery','cms_sitesettings'}:
                raise ControlBlocked(read_only=True)
        return execute(sql,params,many,context)

    def process_exception(self,request,exception):
        if isinstance(exception,ControlBlocked):
            return blocked_response(request,exception)
