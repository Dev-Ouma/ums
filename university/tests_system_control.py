from datetime import timedelta, date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse
from accounts.models import Role, StudentProfile
from .models import (SystemRestriction, Notice, MessageDelivery, SystemModule, SystemSubmodule, SystemFeature,
                     SystemPermission, UserPermissionOverride, AuditLog, ControlNotification,
                     AcademicYear, AcademicTerm, Department, Program, MessageTemplate, RecycleBinItem)
from .control_services import (evaluate, transition, tick, permitted, active_messages,
                              render_fields, ControlBlocked, current_status, deliver_messages)
from .control_forms import RestrictionForm
from .control_middleware import SystemControlMiddleware

User=get_user_model()

class SystemControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user(username='control-admin',password='password123',role=Role.ADMIN)
        cls.student=User.objects.create_user(username='control-student',password='password123',role=Role.STUDENT)
        cls.root=User.objects.create_superuser(username='control-root',password='password123',email='root@example.test')
        cls.module=SystemModule.objects.create(code='control-test-finance',name='Finance',route_prefixes=['/manage/api/academic-performance/'])
        cls.sub=SystemSubmodule.objects.create(module=cls.module,code='control-test-sub',name='Ledger',route_names=['api_academic_performance'])
        cls.feature=SystemFeature.objects.create(submodule=cls.sub,code='control-test-feature',name='Financial reporting',route_names=['api_academic_performance'])
    def restriction(self,**kwargs):
        data={'title':'Database maintenance','kind':'MAINTENANCE','reason':'Private incident details','public_message':'Temporarily unavailable.','status':'ACTIVE','starts_at':timezone.now()-timedelta(minutes=1),'created_by':self.root}
        data.update(kwargs)
        return SystemRestriction.objects.create(**data)
    def grant(self,user,code):
        p,_=SystemPermission.objects.get_or_create(code='control.'+code,defaults={'name':code,'module':'System control'})
        UserPermissionOverride.objects.update_or_create(user=user,permission=p,defaults={'override_type':'GRANT'})
    def test_admin_label_never_grants_control_or_bypass(self):
        self.assertFalse(permitted(self.admin,'maintenance.bypass'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('control:dashboard')).status_code,403)
        self.restriction()
        self.assertEqual(self.client.get('/manage/api/academic-performance/').status_code,503)
    def test_public_and_direct_api_blocked_without_private_reason(self):
        self.restriction()
        r=self.client.get('/about/')
        self.assertEqual(r.status_code,503)
        self.assertNotContains(r,'Private incident details',status_code=503)
        r=self.client.post('/manage/api/academic-performance/',{},HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code,503)
        self.assertEqual(r.json()['status'],'UNAVAILABLE')
        self.assertEqual(self.client.get('/system-control/status/').status_code,200)
    def test_regular_login_rejected_and_recovery_login_preserved(self):
        self.restriction(allow_bypass=False)
        response=self.client.post(reverse('accounts:login'),{'username':self.student.username,'password':'password123'})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('_auth_user_id',self.client.session)
        response=self.client.post(reverse('accounts:login'),{'username':self.root.username,'password':'password123'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,reverse('control:dashboard'))
        self.assertEqual(self.client.get(reverse('control:dashboard')).status_code,200)
        self.assertEqual(self.client.get('/about/').status_code,503)
    def test_explicit_bypass_and_deny(self):
        self.restriction()
        self.grant(self.admin,'maintenance.bypass')
        evaluate(self.admin)
        UserPermissionOverride.objects.filter(user=self.admin).update(override_type='DENY')
        with self.assertRaises(ControlBlocked): evaluate(self.admin)
    def test_read_only_all_mutating_methods(self):
        self.restriction(kind='READ_ONLY')
        self.client.force_login(self.admin)
        for method in ['post','put','patch','delete']:
            r=getattr(self.client,method)('/manage/api/academic-performance/',HTTP_ACCEPT='application/json')
            self.assertEqual(r.status_code,423,method)
        self.assertIsNotNone(evaluate(self.admin,write=False))
        self.assertEqual(self.client.get('/about/').status_code,200)
    def test_read_only_database_guard_catches_get_side_effect(self):
        from django.db import connection, transaction
        with connection.execute_wrapper(SystemControlMiddleware.write_guard):
            with self.assertRaises(ControlBlocked):
                with transaction.atomic():
                    Department.objects.create(code='BLOCKED',name='Must not exist')
        self.assertFalse(Department.objects.filter(code='BLOCKED').exists())
    def test_module_and_feature_scope_and_restoration(self):
        r=self.restriction(kind='MODULE');r.modules.add(self.module)
        with self.assertRaises(ControlBlocked): evaluate(self.student,path='/manage/api/academic-performance/',route='api_academic_performance',namespace='university')
        evaluate(self.student,path='/about/',route='about',namespace='university')
        self.module.status='DISABLED';self.module.save()
        transition(r.pk,'complete',self.root)
        self.module.refresh_from_db();self.assertEqual(self.module.status,'DISABLED')
        with self.assertRaises(ControlBlocked): evaluate(self.student,path='/manage/api/academic-performance/',route='api_academic_performance',namespace='university')
        self.module.status='ENABLED';self.module.save()
        r=self.restriction(kind='MODULE');r.features.add(self.feature)
        with self.assertRaises(ControlBlocked): evaluate(self.student,route='api_academic_performance',namespace='university')
    def test_role_scope(self):
        self.restriction(kind='ROLE',roles=['STUDENT'])
        evaluate(self.admin)
        with self.assertRaises(ControlBlocked): evaluate(self.student)
    def test_schedule_enforces_before_tick_and_expires_at_boundary(self):
        now=timezone.now()
        r=self.restriction(status='SCHEDULED',ends_at=now+timedelta(minutes=1))
        with self.assertRaises(ControlBlocked): evaluate(self.student)
        with patch('university.control_services.timezone.now',return_value=r.ends_at):
            evaluate(self.student);tick()
        r.refresh_from_db();self.assertEqual(r.status,'COMPLETED')
    def test_overlapping_restrictions_do_not_restore_prematurely(self):
        first=self.restriction();second=self.restriction(kind='LOCKDOWN')
        transition(second.pk,'complete',self.root)
        with self.assertRaises(ControlBlocked): evaluate(self.student)
        transition(first.pk,'complete',self.root);evaluate(self.student)
    def test_emergency_requires_permission_and_exact_phrase(self):
        r=self.restriction(kind='EMERGENCY',status='DRAFT')
        with self.assertRaises(ValidationError): transition(r.pk,'activate',self.root,phrase='yes')
        transition(r.pk,'activate',self.root,phrase='ENABLE EMERGENCY LOCKDOWN')
        self.assertEqual(current_status()['status'],'Emergency Lockdown')
        self.assertTrue(AuditLog.objects.filter(entity='SystemRestriction',entity_id=str(r.pk)).exists())
    def test_logout_sessions_excludes_recovery_admin(self):
        self.client.force_login(self.student);key=self.client.session.session_key
        r=self.restriction(status='DRAFT',session_policy='LOGOUT')
        transition(r.pk,'activate',self.root)
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.client.force_login(self.root);key=self.client.session.session_key
        from .control_services import terminate_sessions
        terminate_sessions(r)
        self.assertTrue(Session.objects.filter(session_key=key).exists())
    def test_existing_session_read_only_new_login_blocked(self):
        r=self.restriction(session_policy='READ_ONLY')
        self.assertEqual(evaluate(self.student,session_started=(r.starts_at-timedelta(hours=1)).timestamp()),r)
        with self.assertRaises(ControlBlocked): evaluate(self.student,login=True)
        with self.assertRaises(ControlBlocked): evaluate(self.student,write=True,session_started=(r.starts_at-timedelta(hours=1)).timestamp())
    def test_message_audience_window_and_expiry(self):
        n=Notice.objects.create(title='Private',body='Only student',audience='ALL',status='SCHEDULED',starts_at=timezone.now()-timedelta(seconds=1),ends_at=timezone.now()+timedelta(hours=1))
        n.recipients.add(self.student)
        self.assertEqual([x.pk for x in active_messages(self.student)],[n.pk])
        self.assertEqual(active_messages(self.admin),[])
        self.assertEqual(active_messages(None),[])
        with patch('university.control_services.timezone.now',return_value=n.ends_at):
            self.assertEqual(active_messages(self.student),[])
    def test_warning_and_delivery_idempotency(self):
        r=self.restriction(status='SCHEDULED',starts_at=timezone.now()+timedelta(minutes=4),ends_at=timezone.now()+timedelta(hours=1),warning_minutes=[5])
        tick();tick()
        self.assertEqual(ControlNotification.objects.filter(restriction=r,event='warning:5').count(),1)
        deliver_messages();deliver_messages()
        n=ControlNotification.objects.get(restriction=r,event='warning:5').message
        self.assertEqual(MessageDelivery.objects.filter(message=n,recipient=self.student).count(),1)
    def test_dynamic_academic_fields_and_templates(self):
        year=AcademicYear.objects.create(name='AY control',start_date=date(2026,1,1),end_date=date(2026,12,31),is_current=True)
        term=AcademicTerm.objects.create(name='Control term',academic_year=year,start_date=date(2026,1,1),end_date=date(2026,6,30),registration_end_date=date(2026,1,20),is_current=True)
        self.assertEqual(render_fields('{{academic_year}} / {{semester}} / {{registration_deadline}}',self.student),'AY control / Control term / 2026-01-20')
        self.assertTrue(MessageTemplate.objects.filter(name='Maintenance').exists())
    def test_receipt_cannot_target_another_users_message(self):
        n=Notice.objects.create(title='Private',body='Secret');n.recipients.add(self.root)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(reverse('control:receipt',args=[n.pk,'read'])).status_code,404)
        self.assertFalse(MessageDelivery.objects.filter(recipient=self.student,message=n).exists())
    def test_important_message_confirmation_and_recycle_restore_draft(self):
        self.client.force_login(self.root)
        n=Notice.objects.create(title='Critical',body='Notice',priority='CRITICAL')
        self.client.post(reverse('control:message_action',args=[n.pk,'delete']))
        n.refresh_from_db();self.assertEqual(n.status,'PUBLISHED')
        self.client.post(reverse('control:message_action',args=[n.pk,'delete']),{'confirm':'yes'})
        n.refresh_from_db();self.assertEqual(n.status,'DELETED')
        item=RecycleBinItem.objects.get(content_type='SystemMessage',object_id=str(n.pk))
        from .recycle_bin_services import restore_from_recycle_bin
        restored,_=restore_from_recycle_bin(item.pk,user=self.root)
        self.assertEqual(restored.status,'DRAFT')
    def test_control_pages_render(self):
        self.client.force_login(self.root)
        for name in ['dashboard','restriction_create','messages','message_create','templates','template_create','health']:
            self.assertEqual(self.client.get(reverse('control:'+name)).status_code,200,name)
        r=self.restriction(status='DRAFT');n=Notice.objects.create(title='Test',body='Test')
        self.assertEqual(self.client.get(reverse('control:restriction_detail',args=[r.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('control:message_detail',args=[n.pk])).status_code,200)
    def test_post_only_actions_and_csrf(self):
        from django.test import Client
        self.client.force_login(self.root)
        r=self.restriction(status='DRAFT')
        url=reverse('control:restriction_action',args=[r.pk,'activate'])
        self.assertEqual(self.client.get(url).status_code,405)
        c=Client(enforce_csrf_checks=True);c.force_login(self.root)
        self.assertEqual(c.post(url).status_code,403)
    def test_draft_create_requires_permissions(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('control:restriction_create'),{'kind':'EMERGENCY'}).status_code,403)

    def test_system_admin_url_aliases(self):
        """Verify all /system-admin/ and /manage/system/ route aliases redirect cleanly."""
        self.client.force_login(self.root)
        aliases = [
            ('/system-admin/maintenance/', '/system-control/'),
            ('/system-admin/messages/', '/system-control/messages/'),
            ('/system-admin/lockdown/', '/system-control/?section=lockdown'),
            ('/system-admin/status/', '/system-control/status/'),
            ('/system-admin/health/', '/system-control/health/'),
            ('/manage/system/maintenance/', '/system-control/'),
            ('/manage/system/messages/', '/system-control/messages/'),
            ('/manage/system/lockdown/', '/system-control/?section=lockdown'),
        ]
        for url, expected_target in aliases:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 302, f"Alias {url} should redirect")
            self.assertEqual(res.url, expected_target, f"Alias {url} redirected incorrectly")

    def test_system_control_navigation_rendered_for_admin(self):
        """Verify System Maintenance, Messages, and Lockdown links appear in the sidebar."""
        self.client.force_login(self.root)
        res = self.client.get(reverse('control:dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "System Maintenance")
        self.assertContains(res, "System Messages")
        self.assertContains(res, "Lockdown &amp; Security")
        self.assertContains(res, "Module Management")

    def test_unavailable_page_rendering(self):
        """Verify the custom branded unavailable template renders cleanly on active restriction."""
        self.restriction(kind='MAINTENANCE', public_message="Campus electrical grid upgrade in progress.")
        res = self.client.get('/about/')
        self.assertEqual(res.status_code, 503)
        self.assertContains(res, "System Temporarily Unavailable", status_code=503)
        self.assertContains(res, "Campus electrical grid upgrade in progress.", status_code=503)
        self.assertContains(res, "Expected Restoration", status_code=503)

    def test_backup_create_snapshot(self):
        """Verify one-click backup creates a snapshot file, logs audit entry, and redirects to health."""
        self.client.force_login(self.root)
        res = self.client.post(reverse('control:backup_create'))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse('control:health'))

        # Check that an AuditLog entry was created
        log = AuditLog.objects.filter(module=AuditLog.Module.CONFIG, entity="DatabaseBackup").latest("timestamp")
        self.assertEqual(log.action, AuditLog.Action.CREATE)
        self.assertIn("ums_backup_", log.entity_id)

        # Check health page lists the created snapshot
        health_res = self.client.get(reverse('control:health'))
        self.assertEqual(health_res.status_code, 200)
        self.assertContains(health_res, "ums_backup_")

    def test_public_status_page(self):
        """Verify public status page renders at /status/ without requiring authentication."""
        # 1. Normal operational state
        res = self.client.get(reverse('university:public_status'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "University System Status")
        self.assertContains(res, "All Systems Operational")
        self.assertContains(res, "Core Services &amp; Portals")
        self.assertContains(res, "Student Portal &amp; Biodata")
        self.assertContains(res, "Tuition Fees &amp; Payments")

        # 2. Active maintenance state reflects immediately on public status
        restr = self.restriction(kind='MAINTENANCE', public_message="Quarterly core network maintenance.")
        res_maint = self.client.get(reverse('university:public_status'))
        self.assertEqual(res_maint.status_code, 200)
        self.assertContains(res_maint, "System Maintenance Underway")
        self.assertContains(res_maint, "Quarterly core network maintenance.")

    def test_heartbeat_status_api_upcoming_window(self):
        """Verify the /system-control/status/ JSON API returns upcoming maintenance info."""
        # Schedule future maintenance 10 minutes from now
        future_start = timezone.now() + timedelta(minutes=10)
        SystemRestriction.objects.create(
            title='Upcoming Database Patch',
            kind='MAINTENANCE',
            status='SCHEDULED',
            starts_at=future_start,
            ends_at=future_start + timedelta(hours=1),
            public_message='Scheduled patch window in 10 minutes.',
            created_by=self.root,
        )

        res = self.client.get('/system-control/status/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('upcoming', data)
        self.assertIsNotNone(data['upcoming'])
        self.assertIn('minutes_until', data['upcoming'])
        self.assertEqual(data['upcoming']['message'], 'Scheduled patch window in 10 minutes.')
