from django.urls import path

from . import views
from . import academics_views
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

app_name = "university"

urlpatterns = [
    # Public
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("catalog/", views.courses_public, name="courses_public"),

    # Dashboard router
    path("dashboard/", views.dashboard, name="dashboard"),

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

    # Admin — departments & programs
    path("manage/departments/", views.admin_departments, name="admin_departments"),
    path("manage/departments/new/", views.department_create, name="department_create"),
    path("departments/<int:pk>/", views.department_detail, name="department_detail"),
    path("manage/departments/<int:pk>/edit/", views.department_edit, name="department_edit"),
    path("manage/departments/<int:pk>/delete/", views.department_delete, name="department_delete"),
    path("manage/programs/new/", views.program_create, name="program_create"),
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

    # Fees
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
    path("me/fees/statement/", views.student_fee_statement, name="student_fee_statement"),
    path("me/fees/statement/pdf/", views.student_fee_statement_pdf, name="student_fee_statement_pdf"),

    # AI
    path("ai/assistant/", views.ai_assistant, name="ai_assistant"),
    path("ai/reply/", views.ai_reply, name="ai_reply"),
    path("ai/insights/", views.ai_insights, name="ai_insights"),

    # Admissions (Public & Admin)
    path("admissions/apply/", admissions_views.apply, name="admissions_apply"),
    path("admissions/status/", admissions_views.application_status, name="admissions_status"),
    path("admissions/<int:pk>/letter/", admissions_views.download_admission_letter, name="download_admission_letter"),
    path("manage/admissions/", admissions_views.admin_admissions_list, name="admin_admissions"),
    path("manage/admissions/<int:pk>/", admissions_views.admin_admission_detail, name="admin_admission_detail"),
    path("manage/admissions/<int:pk>/matriculate/", admissions_views.admin_admission_matriculate, name="admin_admission_matriculate"),
    path("manage/admissions/intakes/", admissions_views.admin_intakes, name="admin_intakes"),

    # Academics — Student
    path("academics/register/", academics_views.student_register_units, name="student_register_units"),
    path("academics/exam-card/", academics_views.student_exam_card, name="student_exam_card"),
    path("academics/exam-card/pdf/", academics_views.student_exam_card_pdf, name="student_exam_card_pdf"),
    path("academics/supplementary/", academics_views.student_supplementary, name="student_supplementary"),
    path("academics/supplementary/<int:course_id>/apply/", academics_views.student_supplementary_apply, name="student_supplementary_apply"),
    path("academics/provisional-transcript/", academics_views.student_provisional_transcript_view, name="student_provisional_transcript"),
    path("academics/academic-transcript/", academics_views.student_academic_transcript_view, name="student_academic_transcript"),

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

    # University Reporting System & Analytics Hub
    path("manage/reports/", reporting_views.admin_reports_dashboard, name="admin_reports_dashboard"),
    path("manage/reports/<str:report_key>/", reporting_views.report_view, name="report_view"),
    path("manage/reports/<str:report_key>/export/<str:fmt>/", reporting_views.export_report, name="export_report"),
    path("teach/reports/", reporting_views.faculty_reports, name="faculty_reports"),
    path("me/reports/", reporting_views.student_reports, name="student_reports"),
]
