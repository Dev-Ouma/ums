from contextlib import ExitStack
import re
from django.contrib.auth import logout
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import resolve, Resolver404
from .control_services import ControlBlocked, evaluate, permitted, render_fields


def blocked_response(request, error):
    r=error.restriction
    text=str(error) if error.read_only or not r else render_fields(r.public_message,request.user,r)
    data={'status':'READ_ONLY' if error.read_only else 'UNAVAILABLE','message':text,
          'expected_restoration':r.ends_at.isoformat() if r and r.ends_at else None}
    status=423 if error.read_only else 503
    if 'application/json' in request.headers.get('Accept','') or '/api/' in request.path or request.headers.get('X-Requested-With')=='XMLHttpRequest':
        response=JsonResponse(data,status=status)
    else:
        response=render(request,'control/unavailable.html',{'availability':data,'restoration':r.ends_at if r else None},status=status)
    response['Cache-Control']='no-store, private'
    response['Retry-After']='60'
    return response


class SystemControlMiddleware:
    def __init__(self,get_response):
        self.get_response=get_response

    def __call__(self,request):
        from django.utils import timezone
        from .models import ControlHeartbeat
        from .control_services import tick
        heartbeat = ControlHeartbeat.objects.filter(key='scheduler').first()
        if not heartbeat or (timezone.now() - heartbeat.last_success_at).total_seconds() >= 30:
            tick()
        try:
            match=resolve(request.path_info)
            route,namespace=match.url_name,match.namespace
        except Resolver404:
            route,namespace='',''
        # Narrow recovery surface, still subject to per-action authorization and CSRF.
        recovery=namespace=='control' and any(permitted(request.user,p) for p in ['maintenance.deactivate','lockdown.deactivate'])
        exempt=((namespace=='accounts' and route in {'login','logout'}) or
                (namespace=='control' and route=='status') or
                route=='public_status' or
                request.path_info.startswith('/status/'))
        if recovery or exempt:
            response=self.get_response(request)
            response['Cache-Control']='no-store, private'
            return response
        try:
            read_only=evaluate(request.user,path=request.path_info,route=route,namespace=namespace,
                               write=request.method not in {'GET','HEAD','OPTIONS'},session_started=request.session.get('_control_login_at') or (request.user.last_login.timestamp() if request.user.is_authenticated and request.user.last_login else None))
        except ControlBlocked as error:
            r=error.restriction
            if not r:
                # Underlying module state (Maintenance, Coming Soon, Inactive) is handled
                # by ModuleAccessMiddleware with rich customized messages and branded templates.
                return self.get_response(request)
            if r and r.session_policy=='LOGOUT' and request.user.is_authenticated:
                # Never terminate an incident manager who needs the recovery surface.
                if not any(permitted(request.user,p) for p in ['maintenance.deactivate','lockdown.deactivate']):
                    logout(request)
            return blocked_response(request,error)
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
