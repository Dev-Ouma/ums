from django.urls import path

from . import views
from . import academics_views
from . import student_requests_views
from . import admissions_views
from . import attachment_views
from . import audit_views
from . import graduation_views
from . import hostel_views
from . import library_views
from . import recycle_bin_views
from . import setups_views
from . import evaluation_views
from . import reporting_views
from . import permissions_views
from . import verification_views
from . import calendar_views
from . import admission_document_views
from . import module_views
from . import fee_payment_views
from . import fee_account_views
from . import backup_views
from . import identity_views
from . import golive_views

app_name = "university"

urlpatterns = [
    # Public & Verification
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("status/", views.public_status, name="public_status"),
    path("catalog/", views.courses_public, name="courses_public"),
    path("verify/document/", verification_views.public_verify_document, name="verify_document_query"),
    path("verify/document/<path:reference_no>/", verification_views.public_verify_document, name="verify_document"),

    # Dashboard router
    path("dashboard/", views.dashboard, name="dashboard"),
    path("manage/", views.dashboard, name="manage_dashboard"),
    path("manage/api/academic-performance/", views.api_academic_performance, name="api_academic_performance"),
    path("manage/api/hierarchy/", views.api_academic_hierarchy, name="api_academic_hierarchy"),

    # Admin — Academic Years & Semesters (Central Academic Calendar)
    path("manage/academic-years/", calendar_views.admin_academic_years, name="admin_academic_years"),
    path("manage/academic-calendar/", calendar_views.admin_academic_years, name="admin_academic_calendar"),
    path("manage/academic-years/new/", calendar_views.academic_year_create, name="academic_year_create"),
    path("manage/academic-years/<int:pk>/", calendar_views.academic_year_detail, name="academic_year_detail"),
    path("manage/academic-years/<int:pk>/edit/", calendar_views.academic_year_edit, name="academic_year_edit"),
    path("manage/academic-years/<int:pk>/delete/", calendar_views.academic_year_delete, name="academic_year_delete"),
    path("manage/academic-years/<int:pk>/action/<str:action>/", calendar_views.academic_year_action, name="academic_year_action"),

    # Semesters under Academic Years
    path("manage/semester/", calendar_views.admin_semesters, name="admin_semesters"),
    path("manage/semesters/", calendar_views.admin_semesters, name="admin_semesters_plural"),
    path("manage/semester/new/", calendar_views.semester_create_global, name="semester_create_global"),
    path("manage/academic-years/<int:year_id>/semesters/new/", calendar_views.semester_create, name="semester_create"),
    path("manage/semesters/<int:pk>/edit/", calendar_views.semester_edit, name="semester_edit"),
    path("manage/semesters/<int:pk>/delete/", calendar_views.semester_delete, name="semester_delete"),
    path("manage/semesters/<int:pk>/action/<str:action>/", calendar_views.semester_action, name="semester_action"),

    # Student Numbering API & Setup
    path("manage/api/numbering-preview/", calendar_views.api_numbering_preview, name="api_numbering_preview"),
    path("manage/academic-calendar/numbering/", calendar_views.admin_save_numbering_config, name="admin_save_numbering_config"),

    # Admin — students
    path("manage/students/", views.admin_students, name="admin_students"),
    path("manage/students/new/", views.student_create, name="student_create"),
    path("manage/students/export/<str:fmt>/", views.student_export, name="student_export"),
    path("manage/students/preview/", views.student_preview, name="student_preview"),
    path("manage/students/import/", views.student_import, name="student_import"),
    path("manage/students/import/template/<str:fmt>/", views.student_import_template,
         name="student_import_template"),
    path("manage/students/<int:pk>/", views.student_detail, name="student_detail"),
    path("manage/students/<int:pk>/edit/", views.student_edit, name="student_edit"),
    path("manage/students/<int:pk>/delete/", views.student_delete, name="student_delete"),

    # Admin — faculty
    path("manage/faculty/", views.admin_faculty, name="admin_faculty"),
    path("manage/faculty/new/", views.faculty_create, name="faculty_create"),
    path("manage/faculty/export/<str:fmt>/", views.faculty_export, name="faculty_export"),
    path("manage/faculty/preview/", views.faculty_preview, name="faculty_preview"),
    path("manage/faculty/import/", views.faculty_import, name="faculty_import"),
    path("manage/faculty/import/template/<str:fmt>/", views.faculty_import_template,
         name="faculty_import_template"),
    path("manage/faculty/<int:pk>/", views.faculty_detail, name="faculty_detail"),
    path("manage/faculty/<int:pk>/edit/", views.faculty_edit, name="faculty_edit"),
    path("manage/faculty/<int:pk>/delete/", views.faculty_delete, name="faculty_delete"),

    # Admin — schools / faculties
    path("manage/schools/", views.admin_schools, name="admin_schools"),
    path("manage/schools/new/", views.school_create, name="school_create"),
    path("schools/<int:pk>/", views.school_detail, name="school_detail"),
    path("manage/schools/<int:pk>/edit/", views.school_edit, name="school_edit"),
    path("manage/schools/<int:pk>/delete/", views.school_delete, name="school_delete"),

    # Admin — departments & programmes
    path("manage/departments/", views.admin_departments, name="admin_departments"),
    path("manage/departments/new/", views.department_create, name="department_create"),
    path("departments/<int:pk>/", views.department_detail, name="department_detail"),
    path("manage/departments/<int:pk>/edit/", views.department_edit, name="department_edit"),
    path("manage/departments/<int:pk>/delete/", views.department_delete, name="department_delete"),

    # Admin — programmes
    path("manage/programs/", views.admin_programs, name="admin_programs"),
    path("manage/programmes/", views.admin_programs, name="admin_programmes"),
    path("manage/programs/new/", views.program_create, name="program_create"),
    path("manage/programs/export/<str:fmt>/", views.program_export, name="program_export"),
    path("manage/programs/preview/", views.program_preview, name="program_preview"),
    path("manage/programs/import/", views.program_import, name="program_import"),
    path("manage/programs/import/template/<str:fmt>/", views.program_import_template, name="program_import_template"),
    path("manage/programs/<int:pk>/", views.program_detail, name="program_detail"),
    path("manage/programs/<int:pk>/edit/", views.program_edit, name="program_edit"),
    path("manage/programs/<int:pk>/delete/", views.program_delete, name="program_delete"),

    # Courses (admin CRUD + enrollment)
    path("courses/", views.admin_courses, name="admin_courses"),
    path("courses/new/", views.course_create, name="course_create"),
    path("courses/export/<str:fmt>/", views.course_export, name="course_export"),
    path("courses/preview/", views.course_preview, name="course_preview"),
    path("courses/import/", views.course_import, name="course_import"),
    path("courses/import/template/<str:fmt>/", views.course_import_template,
         name="course_import_template"),
    path("courses/<int:pk>/", views.course_detail, name="course_detail"),
    path("courses/<int:pk>/edit/", views.course_edit, name="course_edit"),
    path("courses/<int:pk>/delete/", views.course_delete, name="course_delete"),
    path("courses/<int:pk>/enroll/", views.course_enroll, name="course_enroll"),
    path("enrollments/<int:pk>/remove/", views.enrollment_remove, name="enrollment_remove"),

    # Fees & Payment Accounts
    path("manage/fees/", views.admin_fees, name="admin_fees"),
    path("manage/fees/export/<str:fmt>/", views.fee_export, name="fee_export"),
    path("manage/fees/preview/", views.fee_preview, name="fee_preview"),
    path("manage/fees/new/", views.fee_create, name="fee_create"),
    path("manage/fees/<int:pk>/pay/", views.record_payment, name="record_payment"),
    path("manage/fees/structures/", views.admin_fee_structures, name="admin_fee_structures"),
    path("manage/fees/structures/new/", views.fee_structure_create, name="fee_structure_create"),
    path("manage/fees/structures/<int:pk>/edit/", views.fee_structure_edit, name="fee_structure_edit"),
    path("manage/fees/structures/<int:pk>/delete/", views.fee_structure_delete, name="fee_structure_delete"),
    path("manage/fees/receipt/<int:pk>/pdf/", views.fee_receipt_pdf, name="fee_receipt_pdf"),

    # Fee Accounts & Payment Gateway Administration
    path("finance/fee-accounts/", fee_account_views.fee_accounts_dashboard, name="fee_accounts_dashboard"),
    path("finance/fee-accounts/new/", fee_account_views.fee_account_create, name="fee_account_create"),
    path("finance/fee-accounts/<int:pk>/", fee_account_views.fee_account_detail, name="fee_account_detail"),
    path("finance/fee-accounts/<int:pk>/edit/", fee_account_views.fee_account_edit, name="fee_account_edit"),
    path("finance/fee-accounts/<int:pk>/toggle-status/", fee_account_views.fee_account_toggle_status, name="fee_account_toggle_status"),
    path("finance/fee-accounts/<int:pk>/set-default/", fee_account_views.fee_account_set_default, name="fee_account_set_default"),
    path("finance/fee-accounts/<int:pk>/test/", fee_account_views.fee_account_test, name="fee_account_test"),
    path("finance/fee-accounts/<int:pk>/delete/", fee_account_views.fee_account_delete, name="fee_account_delete"),

    # Payments Ledger & Admin Verification / Reversal
    path("manage/fees/payments/", fee_account_views.admin_payments_list, name="admin_payments_list"),
    path("manage/fees/payments/<int:pk>/", fee_account_views.admin_payment_detail, name="admin_payment_detail"),
    path("manage/fees/payments/<int:pk>/verify/", fee_account_views.admin_payment_verify, name="admin_payment_verify"),
    path("manage/fees/payments/<int:pk>/reverse/", fee_account_views.admin_payment_reverse, name="admin_payment_reverse"),

    # Reconciliation Control Center
    path("finance/fee-accounts/reconciliation/", fee_account_views.fee_reconciliation_dashboard, name="fee_reconciliation_dashboard"),
    path("finance/fee-accounts/reconciliation/<int:pk>/match/", fee_account_views.fee_reconciliation_match, name="fee_reconciliation_match"),
    path("finance/fee-accounts/reconciliation/import/", fee_account_views.fee_reconciliation_import, name="fee_reconciliation_import"),

    # Secure Payment Callbacks / Webhooks
    path("api/payments/callback/mpesa/", fee_payment_views.mpesa_callback, name="mpesa_callback"),
    path("api/payments/callback/mpesa/validation/", fee_payment_views.mpesa_validation, name="mpesa_validation"),
    path("finance/pay/callback/mpesa/", fee_payment_views.mpesa_callback, name="mpesa_callback_alt"),
    path("finance/pay/callback/mpesa/validation/", fee_payment_views.mpesa_validation, name="mpesa_validation_alt"),
    path("api/payments/callback/card/", fee_payment_views.card_callback, name="card_callback"),
    path("api/payments/callback/bank/", fee_payment_views.bank_callback, name="bank_callback"),

    # Timetable
    path("manage/timetable/", views.admin_timetable, name="admin_timetable"),
    path("manage/timetable/new/", views.class_schedule_create, name="class_schedule_create"),
    path("manage/timetable/export/<str:fmt>/", views.timetable_export, name="timetable_export"),
    path("manage/timetable/preview/", views.timetable_preview, name="timetable_preview"),
    path("manage/timetable/import/", views.timetable_import, name="timetable_import"),
    path("manage/timetable/import/template/<str:fmt>/", views.timetable_import_template,
         name="timetable_import_template"),
    path("manage/timetable/<int:pk>/edit/", views.class_schedule_edit, name="class_schedule_edit"),
    path("manage/timetable/<int:pk>/delete/", views.class_schedule_delete, name="class_schedule_delete"),
    path("manage/timetable/<int:pk>/publish/", views.class_schedule_publish, name="class_schedule_publish"),
    path("teach/timetable/", views.faculty_timetable, name="faculty_timetable"),
    path("me/timetable/", views.student_timetable, name="student_timetable"),

    # Notices & events
    path("notices/", views.notices, name="notices"),
    path("notices/<int:pk>/delete/", views.notice_delete, name="notice_delete"),
    path("events/", views.events, name="events"),
    path("events/new/", views.event_create, name="event_create"),
    path("events/<int:pk>/edit/", views.event_edit, name="event_edit"),
    path("events/<int:pk>/delete/", views.event_delete, name="event_delete"),

    # Faculty
    path("teach/courses/", views.faculty_courses, name="faculty_courses"),
    path("teach/attendance/<int:pk>/", views.faculty_attendance, name="faculty_attendance"),
    path("teach/attendance/<int:pk>/history/", views.faculty_attendance_history,
         name="faculty_attendance_history"),
    path("teach/assignments/", views.faculty_assignments, name="faculty_assignments"),
    path("teach/assignments/new/", views.assignment_create, name="assignment_create"),
    path("teach/assignments/<int:pk>/edit/", views.assignment_edit, name="assignment_edit"),
    path("teach/assignments/<int:pk>/delete/", views.assignment_delete, name="assignment_delete"),
    path("teach/grade/<int:pk>/", views.faculty_grade, name="faculty_grade"),
    path("assignments/<int:pk>/", views.assignment_detail, name="assignment_detail"),
    path("exams/new/", views.exam_create, name="exam_create"),
    path("exams/<int:pk>/edit/", views.exam_edit, name="exam_edit"),
    path("exams/<int:pk>/delete/", views.exam_delete, name="exam_delete"),

    # Student
    path("me/courses/", views.student_courses, name="student_courses"),
    path("me/attendance/", views.student_attendance, name="student_attendance"),
    path("me/results/", views.student_results, name="student_results"),
    path("me/assignments/", views.student_assignments, name="student_assignments"),
    path("me/assignments/<int:pk>/submit/", views.submit_assignment, name="submit_assignment"),
    path("me/fees/", views.student_fees, name="student_fees"),
    path("me/fees/pay/", fee_payment_views.student_pay_fees, name="student_pay_fees"),
    path("me/fees/pay/initiate/", fee_payment_views.student_initiate_payment, name="student_initiate_payment"),
    path("me/fees/pay/<str:reference>/status/", fee_payment_views.student_payment_status, name="student_payment_status"),
    path("me/fees/pay/<str:reference>/poll/", fee_payment_views.student_payment_poll, name="student_payment_poll"),
    path("me/fees/receipt/<str:receipt_number>/", fee_payment_views.student_receipt_view, name="student_receipt_view"),
    path("me/fees/receipt/<str:receipt_number>/pdf/", fee_payment_views.student_receipt_pdf, name="student_receipt_pdf"),
    path("me/fees/statement/", views.student_fee_statement, name="student_fee_statement"),
    path("me/fees/statement/pdf/", views.student_fee_statement_pdf, name="student_fee_statement_pdf"),

    # AI
    path("ai/assistant/", views.ai_assistant, name="ai_assistant"),
    path("ai/reply/", views.ai_reply, name="ai_reply"),
    path("ai/insights/", views.ai_insights, name="ai_insights"),

    # Admissions (Public & Admin)
    path("admissions/apply/", admissions_views.apply, name="admissions_apply"),
    path("admissions/<int:pk>/pay-fee/", admissions_views.pay_application_fee, name="pay_application_fee"),
    path("admissions/status/", admissions_views.application_status, name="admissions_status"),
    path("admissions/<int:pk>/letter/", admissions_views.download_admission_letter, name="download_admission_letter"),
    path("manage/admissions/", admissions_views.admin_admissions_list, name="admin_admissions"),
    path("manage/admissions/<int:pk>/", admissions_views.admin_admission_detail, name="admin_admission_detail"),
    path("manage/admissions/<int:pk>/matriculate/", admissions_views.admin_admission_matriculate, name="admin_admission_matriculate"),
    path("manage/admissions/intakes/", admissions_views.admin_intakes, name="admin_intakes"),

    # Admission Documents, Dynamic Templates & Attachment Management (Admin)
    path("manage/admissions/documents/", admission_document_views.admin_admission_documents_list, name="admin_admission_documents_list"),
    path("manage/admissions/documents/<int:pk>/", admission_document_views.admin_admission_document_detail, name="admin_admission_document_detail"),
    path("manage/admissions/documents/<int:pk>/regenerate/", admission_document_views.admin_regenerate_admission_document, name="admin_regenerate_admission_document"),
    path("manage/admissions/documents/<int:pk>/resend/", admission_document_views.admin_resend_admission_document, name="admin_resend_admission_document"),
    path("manage/admissions/documents/<int:pk>/revoke/", admission_document_views.admin_revoke_admission_document, name="admin_revoke_admission_document"),
    path("manage/admissions/documents/view/<int:doc_id>/", admission_document_views.admin_view_admission_document, name="admin_view_admission_document"),
    path("manage/admissions/documents/download/<int:doc_id>/", admission_document_views.admin_download_admission_document, name="admin_download_admission_document"),
    path("manage/admissions/attachments/<int:attachment_id>/verify/", admission_document_views.admin_verify_attachment, name="admin_verify_attachment"),
    path("manage/admissions/templates/", admission_document_views.admin_templates_list, name="admin_templates_list"),
    path("manage/admissions/templates/new/", admission_document_views.admin_template_editor, name="admin_template_editor"),
    path("manage/admissions/templates/<int:pk>/edit/", admission_document_views.admin_template_editor, name="admin_template_editor"),
    path("manage/admissions/templates/<int:pk>/preview/", admission_document_views.admin_template_preview, name="admin_template_preview"),

    # Student Admission Documents & Application Attachments
    path("me/admission-documents/", reporting_views.student_admission_documents, name="student_admission_documents"),
    path("me/admission-documents/<int:doc_id>/view/", reporting_views.student_view_admission_document, name="student_view_admission_document"),
    path("me/admission-documents/<int:doc_id>/download/", reporting_views.student_download_admission_document, name="student_download_admission_document"),
    path("me/attachments/<int:attachment_id>/download/", reporting_views.student_download_attachment, name="student_download_attachment"),


    # Academics — Student
    path("manage/academics/semestar-reg/", academics_views.student_semester_registration, name="student_semester_registration_legacy"),
    path("manage/academics/semester-reg/", academics_views.student_semester_registration, name="student_semester_registration"),
    path("academics/register/", academics_views.student_register_units, name="student_register_units"),
    path("academics/exam-card/", academics_views.student_exam_card, name="student_exam_card"),
    path("academics/exam-card/pdf/", academics_views.student_exam_card_pdf, name="student_exam_card_pdf"),
    path("academics/supplementary/", academics_views.student_supplementary, name="student_supplementary"),
    path("academics/supplementary/<int:course_id>/apply/", academics_views.student_supplementary_apply, name="student_supplementary_apply"),
    path("academics/provisional-transcript/", academics_views.student_provisional_transcript_view, name="student_provisional_transcript"),
    path("academics/academic-transcript/", academics_views.student_academic_transcript_view, name="student_academic_transcript"),
    path("academics/progressive-report/", academics_views.student_progressive_report_view, name="student_progressive_report"),
    path("academics/requests/", student_requests_views.student_requests, name="student_requests"),
    path("academics/requests/resume/", student_requests_views.student_resume_studies, name="student_resume_studies"),

    # Academics — Admin
    path("manage/academics/", academics_views.admin_academics_dashboard, name="admin_academics_dashboard"),
    path("manage/academics/registrations/", academics_views.admin_unit_registrations, name="admin_unit_registrations"),
    path("manage/academics/registrations/<int:pk>/", academics_views.admin_unit_registration_detail, name="admin_unit_registration_detail"),
    path("manage/academics/supplementary/", academics_views.admin_supplementary_list, name="admin_supplementary_list"),
    path("manage/academics/supplementary/<int:pk>/decision/", academics_views.admin_supplementary_decision, name="admin_supplementary_decision"),
    path("manage/academics/nominal-rolls/", academics_views.admin_exam_nominal_rolls, name="admin_exam_nominal_rolls"),
    path("manage/academics/nominal-rolls/<int:exam_id>/pdf/", academics_views.admin_exam_nominal_roll_pdf, name="admin_exam_nominal_roll_pdf"),
    path("manage/academics/transcripts/provisional/", academics_views.admin_provisional_transcripts, name="admin_provisional_transcripts"),
    path("manage/academics/transcripts/academic/", academics_views.admin_academic_transcripts, name="admin_academic_transcripts"),
    path("manage/academics/progressive-reports/", academics_views.admin_progressive_reports, name="admin_progressive_reports"),
    path("manage/academics/progressive-reports/<int:student_id>/", academics_views.progressive_report_detail_view, name="progressive_report_detail"),
    path("manage/academics/progressive-reports/<int:student_id>/export/<str:fmt>/", academics_views.progressive_report_export_view, name="progressive_report_export"),
    path("manage/academics/requests/", student_requests_views.admin_student_requests, name="admin_student_requests"),
    path("manage/academics/requests/<int:pk>/", student_requests_views.admin_student_request_detail, name="admin_student_request_detail"),
    path("manage/academics/document-controls/", academics_views.admin_document_controls, name="admin_document_controls"),

    # System Administration — Recycle Bin
    path("manage/recycle-bin/", recycle_bin_views.recycle_bin_dashboard, name="recycle_bin_dashboard"),
    path("manage/recycle-bin/<int:pk>/", recycle_bin_views.recycle_bin_detail, name="recycle_bin_detail"),
    path("manage/recycle-bin/<int:pk>/restore/", recycle_bin_views.recycle_bin_restore, name="recycle_bin_restore"),
    path("manage/recycle-bin/bulk-restore/", recycle_bin_views.recycle_bin_bulk_restore, name="recycle_bin_bulk_restore"),
    path("manage/recycle-bin/<int:pk>/purge/", recycle_bin_views.recycle_bin_purge, name="recycle_bin_purge"),
    path("manage/recycle-bin/bulk-purge/", recycle_bin_views.recycle_bin_bulk_purge, name="recycle_bin_bulk_purge"),

    # System Administration — Audit Trails
    path("manage/audit-trails/", audit_views.audit_dashboard, name="audit_dashboard"),
    path("manage/audit-trails/<int:pk>/", audit_views.audit_detail, name="audit_detail"),
    path("manage/audit-trails/export/<str:fmt>/", audit_views.audit_export, name="audit_export"),

    # System Administration — Module Management & Availability Registry
    path("manage/system/modules/", module_views.admin_modules, name="admin_modules"),
    path("system-admin/modules/", module_views.admin_modules, name="system_admin_modules"),
    path("manage/system/modules/<int:pk>/update/", module_views.admin_module_update, name="admin_module_update"),
    path("manage/system/modules/<int:pk>/toggle/", module_views.admin_module_toggle, name="admin_module_toggle"),
    path("manage/system/modules/submodule/<int:pk>/update/", module_views.admin_submodule_update, name="admin_submodule_update"),
    path("manage/system/modules/feature/<int:pk>/toggle/", module_views.admin_feature_toggle, name="admin_feature_toggle"),
    path("manage/system/modules/bulk/", module_views.admin_modules_bulk, name="admin_modules_bulk"),
    path("manage/system/modules/export/", module_views.admin_modules_export_json, name="admin_modules_export_json"),
    path("manage/system/modules/import/", module_views.admin_modules_import_json, name="admin_modules_import_json"),
    path("manage/system/modules/<int:pk>/dependencies/", module_views.admin_module_dependencies_api, name="admin_module_dependencies_api"),

    # System Administration — Scheduled System Backups & Disaster Recovery
    path("system-admin/backups/", backup_views.backup_dashboard, name="backup_dashboard"),
    path("system-admin/backups/schedules/", backup_views.backup_schedules, name="backup_schedules"),
    path("system-admin/backups/schedules/create/", backup_views.backup_schedule_create, name="backup_schedule_create"),
    path("system-admin/backups/schedules/<int:pk>/edit/", backup_views.backup_schedule_edit, name="backup_schedule_edit"),
    path("system-admin/backups/schedules/<int:pk>/toggle/", backup_views.backup_schedule_toggle, name="backup_schedule_toggle"),
    path("system-admin/backups/schedules/<int:pk>/run/", backup_views.backup_schedule_run_now, name="backup_schedule_run_now"),
    path("system-admin/backups/schedules/<int:pk>/delete/", backup_views.backup_schedule_delete, name="backup_schedule_delete"),
    path("system-admin/backups/history/", backup_views.backup_history, name="backup_history"),
    path("system-admin/backups/create/", backup_views.backup_create_now, name="backup_create_now"),
    path("system-admin/backups/<int:pk>/", backup_views.backup_detail, name="backup_detail"),
    path("system-admin/backups/<int:pk>/verify/", backup_views.backup_verify, name="backup_verify"),
    path("system-admin/backups/<int:pk>/download/", backup_views.backup_download, name="backup_download"),
    path("system-admin/backups/<int:pk>/delete/", backup_views.backup_delete, name="backup_delete"),
    path("system-admin/backups/<int:pk>/toggle-protect/", backup_views.backup_toggle_protect, name="backup_toggle_protect"),
    path("system-admin/backups/storage/", backup_views.backup_storage, name="backup_storage"),
    path("system-admin/backups/storage/<int:pk>/test/", backup_views.backup_storage_test, name="backup_storage_test"),
    path("system-admin/backups/restore/", backup_views.backup_restore_dashboard, name="backup_restore_dashboard"),
    path("system-admin/backups/restore/start/", backup_views.backup_restore_start, name="backup_restore_start"),
    path("system-admin/backups/settings/", backup_views.backup_settings, name="backup_settings"),
    path("system-admin/backups/logs/", backup_views.backup_logs, name="backup_logs"),
    path("system-admin/backups/export/", backup_views.backup_export, name="backup_export"),

    # Go-Live Command Center
    path("system-admin/go-live/", golive_views.golive_dashboard, name="golive_dashboard"),
    path("system-admin/go-live/category/<int:pk>/update/", golive_views.golive_category_update, name="golive_category_update"),
    path("system-admin/go-live/issues/create/", golive_views.golive_issue_create, name="golive_issue_create"),
    path("system-admin/go-live/issues/<int:pk>/update/", golive_views.golive_issue_update, name="golive_issue_update"),
    path("system-admin/go-live/issues/<int:pk>/delete/", golive_views.golive_issue_delete, name="golive_issue_delete"),

    # System Administration — Admin Setups
    path("manage/setups/", setups_views.admin_setups_dashboard, name="admin_setups_dashboard"),
    path("manage/setups/<str:category>/update/", setups_views.admin_setups_update, name="admin_setups_update"),

    # Granular Roles, Permissions & Staff User Overrides
    path("manage/setups/permissions/", permissions_views.staff_permissions_dashboard, name="staff_permissions_dashboard"),
    path("manage/setups/permissions/roles/new/", permissions_views.role_create_edit, name="role_create"),
    path("manage/setups/permissions/roles/<int:role_id>/edit/", permissions_views.role_create_edit, name="role_edit"),
    path("manage/setups/permissions/roles/<int:role_id>/delete/", permissions_views.role_delete, name="role_delete"),
    path("manage/setups/permissions/staff/<int:user_id>/", permissions_views.staff_user_access_detail, name="staff_user_access_detail"),
    path("manage/setups/permissions/staff/<int:user_id>/override/", permissions_views.set_permission_override_action, name="set_permission_override_action"),
    path("manage/setups/permissions/staff/<int:user_id>/role/", permissions_views.staff_role_assignment_action, name="staff_role_assignment_action"),

    # Graduation & Multi-Department Clearance (Student)
    path("academics/graduation/", graduation_views.student_graduation_status, name="student_graduation"),
    path("academics/graduation/apply/", graduation_views.student_apply_clearance, name="student_apply_clearance"),
    path("academics/graduation/degree-certificate/pdf/", graduation_views.student_degree_certificate_pdf, name="student_degree_certificate_pdf"),
    path("academics/graduation/clearance-certificate/pdf/", graduation_views.student_clearance_certificate_pdf, name="student_clearance_certificate_pdf"),

    # Graduation & Clearance (Admin)
    path("manage/graduation/", graduation_views.admin_graduation_dashboard, name="admin_graduation_dashboard"),
    path("manage/graduation/ceremony/new/", graduation_views.admin_ceremony_create, name="admin_ceremony_create"),
    path("manage/graduation/clearance/<str:department>/", graduation_views.admin_clearance_queue, name="admin_clearance_queue"),
    path("manage/graduation/clearance/<int:pk>/action/", graduation_views.admin_clearance_action, name="admin_clearance_action"),
    path("manage/graduation/<int:pk>/senate-approve/", graduation_views.admin_senate_approve, name="admin_senate_approve"),

    # Campus Accommodation & Hostels
    path("campus/hostels/", hostel_views.student_hostel_portal, name="student_hostel_portal"),
    path("campus/hostels/apply/", hostel_views.student_hostel_apply, name="student_hostel_apply"),
    path("manage/hostels/", hostel_views.admin_hostels_dashboard, name="admin_hostels_dashboard"),
    path("manage/hostels/<int:pk>/allocate/", hostel_views.admin_hostel_allocate, name="admin_hostel_allocate"),
    path("manage/hostels/<int:pk>/checkin/", hostel_views.admin_hostel_checkin, name="admin_hostel_checkin"),
    path("manage/hostels/<int:pk>/checkout/", hostel_views.admin_hostel_checkout, name="admin_hostel_checkout"),

    # Library & Past Exam Papers
    path("campus/library/", library_views.student_library_portal, name="student_library_portal"),
    path("manage/library/", library_views.admin_library_dashboard, name="admin_library_dashboard"),
    path("manage/library/issue/", library_views.admin_library_issue, name="admin_library_issue"),
    path("manage/library/<int:pk>/return/", library_views.admin_library_return, name="admin_library_return"),
    path("manage/library/books/new/", library_views.admin_library_book_create, name="admin_library_book_create"),
    path("manage/library/past-papers/new/", library_views.admin_past_paper_create, name="admin_past_paper_create"),

    # Industrial Attachment & Practicum Management
    path("academics/attachment/", attachment_views.student_attachment_portal, name="student_attachment_portal"),
    path("academics/attachment/apply/", attachment_views.student_attachment_apply, name="student_attachment_apply"),
    path("academics/attachment/<int:pk>/logbook/submit/", attachment_views.student_attachment_logbook_submit, name="student_attachment_logbook_submit"),
    path("academics/attachment/<int:pk>/letter/pdf/", attachment_views.attachment_intro_letter_pdf, name="attachment_intro_letter_pdf"),
    path("academics/attachment/<int:pk>/logbook/pdf/", attachment_views.attachment_logbook_pdf, name="attachment_logbook_pdf"),
    path("faculty/attachments/", attachment_views.faculty_attachment_dashboard, name="faculty_attachment_dashboard"),
    path("faculty/attachments/<int:pk>/review-log/", attachment_views.faculty_attachment_review_log, name="faculty_attachment_review_log"),
    path("faculty/attachments/<int:pk>/grade/", attachment_views.faculty_attachment_grade, name="faculty_attachment_grade"),
    path("manage/academics/attachments/", attachment_views.admin_attachment_dashboard, name="admin_attachment_dashboard"),
    path("manage/academics/attachments/<int:pk>/action/", attachment_views.admin_attachment_action, name="admin_attachment_action"),

    # Course & Lecturer Evaluation (QA Survey)
    path("me/evaluation/", evaluation_views.student_evaluation_portal, name="student_evaluation_portal"),
    path("me/evaluation/<int:course_id>/submit/", evaluation_views.student_evaluation_submit, name="student_evaluation_submit"),
    path("teach/evaluation/", evaluation_views.faculty_evaluation_dashboard, name="faculty_evaluation_dashboard"),
    path("manage/evaluation/", evaluation_views.admin_evaluation_dashboard, name="admin_evaluation_dashboard"),
    path("manage/evaluation/<int:course_id>/", evaluation_views.admin_evaluation_course_detail, name="admin_evaluation_course_detail"),
    path("manage/evaluation/window/toggle/", evaluation_views.admin_evaluation_window_toggle, name="admin_evaluation_window_toggle"),
    path("manage/evaluation/export/<str:fmt>/", evaluation_views.admin_evaluation_export, name="admin_evaluation_export"),

    # ---------------------------------------------------------------------
    # System Admin → User Management & Identity Administration
    # ---------------------------------------------------------------------
    path("system-admin/users/", identity_views.user_dashboard, name="user_dashboard"),
    path("system-admin/users/all/", identity_views.user_list, name="user_list"),
    path("system-admin/users/create/", identity_views.user_create, name="user_create"),
    path("system-admin/users/students/", identity_views.student_accounts, name="student_accounts"),
    path("system-admin/users/staff/", identity_views.staff_accounts, name="staff_accounts"),
    path("system-admin/users/passwords/", identity_views.password_management, name="password_management"),
    path("system-admin/users/status/", identity_views.account_status_board, name="account_status_board"),
    path("system-admin/users/usernames/", identity_views.username_management, name="username_management"),
    path("system-admin/users/groups/", identity_views.group_list, name="group_list"),
    path("system-admin/users/groups/new/", identity_views.group_edit, name="group_create"),
    path("system-admin/users/groups/<int:pk>/", identity_views.group_edit, name="group_edit"),
    path("system-admin/users/groups/<int:pk>/delete/", identity_views.group_delete, name="group_delete"),
    path("system-admin/users/roles/", identity_views.roles_permissions_redirect, name="user_roles_permissions"),
    path("system-admin/users/emails/", identity_views.email_accounts, name="email_accounts"),
    path("system-admin/users/emails/<int:pk>/<str:action>/", identity_views.email_action, name="email_action"),
    path("system-admin/users/security/", identity_views.login_security, name="login_security"),
    path("system-admin/users/login-history/", identity_views.login_history, name="login_history"),
    path("system-admin/users/activity/", identity_views.user_activity, name="user_activity"),
    path("system-admin/users/bulk/", identity_views.bulk_operations, name="bulk_operations"),
    path("system-admin/users/bulk/template/<str:user_type>/<str:fmt>/", identity_views.bulk_import_template, name="bulk_import_template"),
    path("system-admin/users/bulk/preview/", identity_views.bulk_import_preview, name="bulk_import_preview"),
    path("system-admin/users/bulk/commit/", identity_views.bulk_import_commit, name="bulk_import_commit"),
    path("system-admin/users/bulk/report/<int:pk>/", identity_views.bulk_import_report, name="bulk_import_report"),
    path("system-admin/users/bulk/action/", identity_views.bulk_user_action, name="bulk_user_action"),
    path("system-admin/users/export/<str:fmt>/", identity_views.user_export, name="user_export"),
    path("system-admin/users/settings/", identity_views.user_settings, name="user_settings"),
    path("system-admin/users/settings/test-email/", identity_views.send_test_email_view, name="send_test_email"),
    path("system-admin/users/api/generate-username/", identity_views.api_generate_username, name="api_generate_username"),
    path("system-admin/users/api/generate-password/", identity_views.api_generate_password, name="api_generate_password"),
    path("system-admin/users/api/check-username/", identity_views.api_check_username, name="api_check_username"),
    path("system-admin/users/<int:pk>/", identity_views.user_detail, name="user_detail"),
    path("system-admin/users/<int:pk>/edit/", identity_views.user_edit, name="user_edit"),
    path("system-admin/users/<int:pk>/<str:action>/", identity_views.user_action, name="user_action"),

    # University Reporting System & Analytics Hub
    path("manage/reports/", reporting_views.admin_reports_dashboard, name="admin_reports_dashboard"),
    path("manage/reports/<str:report_key>/", reporting_views.report_view, name="report_view"),
    path("manage/reports/<str:report_key>/export/<str:fmt>/", reporting_views.export_report, name="export_report"),
    path("teach/reports/", reporting_views.faculty_reports, name="faculty_reports"),
    path("me/reports/", reporting_views.student_reports, name="student_reports"),
]
