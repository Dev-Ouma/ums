from functools import wraps
from datetime import timedelta
import shutil
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from .models import (SystemRestriction, Notice, MessageTemplate, MessageDelivery,
                     ControlHeartbeat, SystemModule, RecycleBinItem)
from .control_forms import RestrictionForm, MessageForm, TemplateForm
from .control_services import (require_permission, permitted, family, transition, snapshot,
    audit, current_status, active_messages, render_fields, terminate_sessions, restrictions_at)


def permission(code):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request,*args,**kwargs):
            require_permission(request.user,code)
            return view(request,*args,**kwargs)
        return wrapped
    return decorator


def page(request,qs):
    return Paginator(qs,20).get_page(request.GET.get('page'))


@require_safe
def status(request):
    response=JsonResponse(current_status())
    response['Cache-Control']='no-store'
    return response


@login_required
@require_safe
def dashboard(request):
    if not any(permitted(request.user,p) for p in ['maintenance.view','lockdown.view','maintenance.deactivate','lockdown.deactivate']):
        require_permission(request.user,'maintenance.view')
    qs=SystemRestriction.objects.select_related('created_by','activated_by','completed_by').prefetch_related('modules','submodules','features')
    if request.GET.get('q'): qs=qs.filter(Q(title__icontains=request.GET['q'])|Q(reason__icontains=request.GET['q']))
    if request.GET.get('status'): qs=qs.filter(status=request.GET['status'])
    if request.GET.get('section')=='lockdown': qs=qs.exclude(kind__contains='MAINTENANCE')
    upcoming=SystemRestriction.objects.filter(status='SCHEDULED',starts_at__gt=timezone.now()).order_by('starts_at').first()
    return render(request,'control/dashboard.html',{'state':current_status(),'incidents':page(request,qs),'upcoming':upcoming,
        'active_users':get_user_model().objects.filter(is_active=True,last_seen_at__gte=timezone.now()-timedelta(minutes=15)).count(),
        'last_incident':SystemRestriction.objects.filter(status='COMPLETED').order_by('-completed_at').first(),
        'statuses':SystemRestriction.Status.choices})


@login_required
def restriction_edit(request,pk=None):
    obj=get_object_or_404(SystemRestriction,pk=pk) if pk else SystemRestriction(created_by=request.user)
    kind=request.POST.get('kind',obj.kind or 'MAINTENANCE')
    needed='maintenance' if 'MAINTENANCE' in kind else 'lockdown'
    require_permission(request.user,needed+('.edit' if pk and needed=='maintenance' else '.create' if needed=='maintenance' else '.activate'))
    if pk:
        require_permission(request.user,family(obj)+('.edit' if family(obj)=='maintenance' else '.activate'))
        if obj.status!='DRAFT':
            messages.error(request,'Only drafts can be edited. Complete or cancel the incident and create a new one.'); return redirect('control:dashboard')
    before=snapshot(obj) if pk else None
    form=RestrictionForm(request.POST or None,instance=obj)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            if pk and SystemRestriction.objects.select_for_update().get(pk=pk).status!='DRAFT':
                messages.error(request,'The incident changed while you were editing.'); return redirect('control:dashboard')
            obj=form.save(); audit(obj,'UPDATE' if pk else 'CREATE',request.user,request,before)
        messages.success(request,'Maintenance / lockdown draft saved. Review it before activation.'); return redirect('control:restriction_detail',pk=obj.pk)
    return render(request,'control/form.html',{'form':form,'heading':'Edit incident' if pk else 'Create maintenance or lockdown','help':'Times use the selected time zone. Empty scope and user categories mean the entire system.'})


@login_required
@require_safe
def restriction_detail(request,pk):
    obj=get_object_or_404(SystemRestriction,pk=pk)
    if not permitted(request.user,family(obj)+'.deactivate'):
        require_permission(request.user,family(obj)+'.view')
    return render(request,'control/incident.html',{'incident':obj})


@login_required
@require_POST
def restriction_action(request,pk,action):
    try:
        transition(pk,action,request.user,request,phrase=request.POST.get('phrase',''),notes=request.POST.get('notes',''))
        messages.success(request,'System state updated.')
    except ValidationError as error:
        messages.error(request,' '.join(error.messages))
    return redirect('control:restriction_detail',pk=pk)


@permission('lockdown.emergency')
@require_POST
def sessions(request,pk):
    r=get_object_or_404(SystemRestriction,pk=pk,status='ACTIVE')
    require_permission(request.user,family(r)+'.deactivate')
    if request.POST.get('phrase')!='TERMINATE SELECTED SESSIONS':
        messages.error(request,'Type TERMINATE SELECTED SESSIONS to confirm.')
    else:
        try: ids={int(v.strip()) for v in request.POST.get('user_ids','').split(',') if v.strip()}
        except ValueError: ids=set()
        if not ids:
            messages.error(request,'Supply valid user IDs.')
        else:
            with transaction.atomic():
                terminate_sessions(r,ids); audit(r,'TERMINATE_SESSIONS',request.user,request,{'selected_user_ids':sorted(ids)})
            messages.success(request,'Selected sessions terminated; recovery administrators retained access.')
    return redirect('control:restriction_detail',pk=pk)


@permission('messages.view')
@require_safe
def message_list(request):
    qs=Notice.objects.exclude(status='DELETED').select_related('created_by').order_by('-created_at')
    if request.GET.get('q'): qs=qs.filter(Q(title__icontains=request.GET['q'])|Q(body__icontains=request.GET['q']))
    if request.GET.get('status'): qs=qs.filter(status=request.GET['status'])
    return render(request,'control/messages.html',{'entries':page(request,qs),'statuses':Notice._meta.get_field('status').choices})


@login_required
def message_edit(request,pk=None):
    require_permission(request.user,'messages.edit' if pk else 'messages.create')
    obj=get_object_or_404(Notice,pk=pk) if pk else Notice(created_by=request.user,status='DRAFT')
    if pk and obj.status not in {'DRAFT','PAUSED'}:
        messages.error(request,'Unpublish or pause this message before editing.'); return redirect('control:message_detail',pk=pk)
    before=snapshot(obj) if pk else None
    initial={}
    if not pk and request.GET.get('template'):
        t=get_object_or_404(MessageTemplate,pk=request.GET['template']); initial={'title':t.title,'body':t.body,'template':t.pk}
    form=MessageForm(request.POST or None,instance=obj,initial=initial)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            if pk and Notice.objects.select_for_update().get(pk=pk).status not in {'DRAFT','PAUSED'}:
                messages.error(request,'The message changed while you were editing.'); return redirect('control:message_detail',pk=pk)
            obj=form.save(commit=False); obj.updated_by=request.user; obj.save(); form.save_m2m(); audit(obj,'UPDATE' if pk else 'CREATE',request.user,request,before)
        messages.success(request,'Message draft saved.'); return redirect('control:message_detail',pk=obj.pk)
    return render(request,'control/form.html',{'form':form,'heading':'Edit system message' if pk else 'Create system message','help':f'Message times use {settings.TIME_ZONE}. Audience filters combine: a recipient must match every selected category. Empty filters mean everyone. Dynamic fields are resolved from current records.'})


@permission('messages.view')
@require_safe
def message_detail(request,pk):
    n=get_object_or_404(Notice,pk=pk)
    return render(request,'control/message.html',{'entry':n,'preview_title':render_fields(n.title,request.user,n.restriction),'preview_body':render_fields(n.body,request.user,n.restriction),'deliveries':page(request,n.deliveries.select_related('recipient').order_by('-pk'))})


@login_required
@require_POST
@transaction.atomic
def message_action(request,pk,action):
    n=get_object_or_404(Notice.objects.select_for_update(),pk=pk)
    codes={'publish':'publish','resume':'publish','unpublish':'unpublish','pause':'unpublish','archive':'unpublish','delete':'delete','duplicate':'create','resend':'publish'}
    if action not in codes: raise ValidationError('Invalid action')
    require_permission(request.user,'messages.'+codes[action])
    before=snapshot(n)
    if n.status=='DELETED':
        messages.error(request,'Restore this message from the Recycle Bin first.'); return redirect('control:messages')
    if n.priority in {'HIGH','CRITICAL'} and action in {'publish','unpublish','delete','resume','resend'} and request.POST.get('confirm')!='yes':
        messages.error(request,'Confirm this important message action.'); return redirect('control:message_detail',pk=pk)
    if action=='duplicate':
        relations={k:list(getattr(n,k).all()) for k in ['recipients','target_roles','departments','programmes','modules']}
        n.pk=None; n.status='DRAFT'; n.title=('Copy of '+n.title)[:150]; n.created_by=request.user; n.restriction=None; n.save()
        for key,values in relations.items(): getattr(n,key).set(values)
    elif action=='delete':
        # Retain the message and delivery relationships; restoration cannot republish it.
        RecycleBinItem.objects.create(content_type='SystemMessage',object_id=str(n.pk),object_repr=n.title,module='Notices',serialized_data=before,deleted_by=request.user)
        n.status='DELETED'
    elif action in {'publish','resume'}:
        if n.ends_at and n.ends_at<=timezone.now():
            messages.error(request,'Update the expired end time before publishing.'); return redirect('control:message_detail',pk=pk)
        n.status='SCHEDULED' if n.starts_at and n.starts_at>timezone.now() else 'PUBLISHED'
    elif action=='resend':
        if n.status!='PUBLISHED':
            messages.error(request,'Only published messages can be resent.'); return redirect('control:message_detail',pk=pk)
        n.deliveries.filter(method='EMAIL',status='FAILED').update(status='PENDING')
    else: n.status={'unpublish':'DRAFT','pause':'PAUSED','archive':'ARCHIVED'}[action]
    n.updated_by=request.user; n.save(); audit(n,action.upper(),request.user,request,before)
    messages.success(request,'Message updated.')
    return redirect('control:messages' if action=='delete' else 'control:message_detail',**({} if action=='delete' else {'pk':n.pk}))


@permission('messages.view')
@require_safe
def templates(request):
    return render(request,'control/templates.html',{'entries':MessageTemplate.objects.all().order_by('name')})


@permission('messages.edit')
def template_edit(request,pk=None):
    obj=get_object_or_404(MessageTemplate,pk=pk) if pk else MessageTemplate(updated_by=request.user)
    before=snapshot(obj) if pk else None
    form=TemplateForm(request.POST or None,instance=obj)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            obj=form.save(commit=False); obj.updated_by=request.user; obj.save(); audit(obj,'UPDATE' if pk else 'CREATE',request.user,request,before)
        messages.success(request,'Template saved.'); return redirect('control:templates')
    return render(request,'control/form.html',{'form':form,'heading':'Message template','help':'Use dynamic fields such as {{university_name}}, {{semester}}, {{registration_deadline}}, {{maintenance_start}} and {{maintenance_end}}.'})


@login_required
@require_POST
def receipt(request,pk,action):
    n=next((n for n in active_messages(request.user,include_archived=True) if n.pk==pk),None)
    if not n:
        from django.http import Http404
        raise Http404
    if action not in {'read','unread','archive','unarchive'}:
        from django.http import Http404
        raise Http404
    d,_=MessageDelivery.objects.get_or_create(message=n,recipient=request.user,method='IN_APP')
    now=timezone.now(); d.viewed_at=d.viewed_at or now; d.delivered_at=d.delivered_at or now; d.sent_at=d.sent_at or now; d.status='DELIVERED'
    if action in {'read','unread'}: d.read_at=now if action=='read' else None
    else: d.archived_at=now if action=='archive' else None
    d.save()
    return redirect('university:notices')


@permission('health.view')
@require_safe
def health(request):
    try:
        with connection.cursor() as cursor: cursor.execute('SELECT 1'); cursor.fetchone()
        database='Connected'
    except Exception: database='Unavailable'
    scheduler=ControlHeartbeat.objects.filter(key='scheduler').first()
    delivery=ControlHeartbeat.objects.filter(key='delivery').first()
    disk=shutil.disk_usage(settings.MEDIA_ROOT if settings.MEDIA_ROOT.exists() else settings.BASE_DIR)
    checks=[('Application','Responding'),('Database',database),('Storage free',f'{disk.free//(1024**3)} GB'),
            ('Scheduler last success',scheduler.last_success_at if scheduler else 'Not yet run'),
            ('Delivery worker last success',delivery.last_success_at if delivery else 'Not yet run'),
            ('Email','Configured' if getattr(settings,'SYSTEM_CONTROL_EMAIL_ENABLED',False) else 'Not configured'),
            ('SMS','Not configured'),('Backup monitoring','Not configured')]
    return render(request,'control/health.html',{'checks':checks,'state':current_status()})
