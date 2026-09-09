"""
Centralized Module Management Engine for University Management System.
Controls module discovery, hierarchical registry, caching, dependency resolution,
safe status transitions, backend enforcement, and forensic audit logging.
"""

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from university.module_models import (
    ModuleStatus,
    SystemModule,
    SystemSubmodule,
    SystemFeature,
    ModuleDependency,
)
from university.audit_services import log_activity
from university.models import AuditLog

CACHE_KEY_MODULE_REGISTRY = "ums_system_module_registry_v1"
CACHE_TIMEOUT_SECONDS = 3600  # 1 hour (invalidated on any edit)


# ==============================================================================
# 1. COMPREHENSIVE SEEDING DEFINITIONS FOR ALL EXISTING UMS MODULES
# ==============================================================================

SYSTEM_MODULES_CATALOG = [
    {
        "code": "auth",
        "name": "Authentication & Security",
        "category": "Governance & Security",
        "icon": "fa-solid fa-shield-halved",
        "description": "User authentication, sessions, profile management, and account security controls.",
        "is_critical": True,
        "sort_order": 1,
        "target_roles": ["ALL"],
        "route_prefixes": ["accounts:"],
        "submodules": [
            {
                "code": "auth_login",
                "name": "Login & Authentication",
                "description": "User login, password verification, and session lifecycle.",
                "icon": "fa-solid fa-right-to-bracket",
                "is_critical": True,
                "route_names": ["login", "logout", "signup"],
                "path_patterns": ["/accounts/login/", "/accounts/logout/", "/accounts/signup/"],
            },
            {
                "code": "auth_profile",
                "name": "User Profile & Settings",
                "description": "Personal account information and profile customization.",
                "icon": "fa-solid fa-user-gear",
                "is_critical": True,
                "route_names": ["profile", "profile_settings"],
                "path_patterns": ["/accounts/profile/"],
            },
        ],
    },
    {
        "code": "system_admin",
        "name": "System Administration",
        "category": "Governance & Security",
        "icon": "fa-solid fa-gears",
        "description": "Central system administration, module governance, institutional setups, and forensic audit trails.",
        "is_critical": True,
        "sort_order": 2,
        "target_roles": ["ADMIN"],
        "route_prefixes": ["university:admin_modules", "university:admin_setups_dashboard", "university:staff_permissions_dashboard", "university:audit_dashboard", "university:recycle_bin_dashboard", "university:backup_dashboard"],
        "submodules": [
            {
                "code": "module_mgmt",
                "name": "Module Management & Registry",
                "description": "Configure availability, maintenance modes, and feature dependencies across UMS.",
                "icon": "fa-solid fa-cubes-stacked",
                "is_critical": True,
                "route_names": ["admin_modules", "admin_module_update", "admin_module_toggle", "admin_submodule_update", "admin_feature_toggle", "admin_modules_bulk", "admin_module_dependencies_api"],
                "path_patterns": ["/manage/system/modules/"],
            },
            {
                "code": "admin_setups",
                "name": "Admin Setups & Configuration",
                "description": "Institution settings, university structure, timetable, branding, and academic configuration.",
                "icon": "fa-solid fa-sliders",
                "is_critical": False,
                "route_names": ["admin_setups_dashboard", "admin_setups_update"],
                "path_patterns": ["/manage/setups/"],
            },
            {
                "code": "permissions",
                "name": "Staff Roles & Access Permissions",
                "description": "Granular roles, permission catalogs, and explicit user-level override policies.",
                "icon": "fa-solid fa-user-shield",
                "is_critical": False,
                "route_names": ["staff_permissions_dashboard", "role_create", "role_edit", "role_delete", "staff_user_access_detail", "set_permission_override_action", "staff_role_assignment_action"],
                "path_patterns": ["/manage/setups/permissions/"],
            },
            {
                "code": "audit_trails",
                "name": "Audit Trails & Security Forensics",
                "description": "Complete forensic ledger of state modifications, data exports, and administrative actions.",
                "icon": "fa-solid fa-list-check",
                "is_critical": True,
                "route_names": ["audit_dashboard", "audit_detail", "audit_export"],
                "path_patterns": ["/manage/audit-trails/"],
            },
            {
                "code": "recycle_bin",
                "name": "Recycle Bin & Data Recovery",
                "description": "Soft-deleted records recovery center with bulk restoration and permanent purge controls.",
                "icon": "fa-solid fa-trash-can-arrow-up",
                "is_critical": False,
                "route_names": ["recycle_bin_dashboard", "recycle_bin_detail", "recycle_bin_restore", "recycle_bin_bulk_restore", "recycle_bin_purge", "recycle_bin_bulk_purge"],
                "path_patterns": ["/manage/recycle-bin/"],
            },
            {
                "code": "system_backups",
                "name": "System Backups & Disaster Recovery",
                "description": "Automated recurring schedules, database snapshots, media packaging, and safe recovery.",
                "icon": "fa-solid fa-database",
                "is_critical": True,
                "route_names": [
                    "backup_dashboard", "backup_schedules", "backup_schedule_create", "backup_schedule_edit",
                    "backup_schedule_toggle", "backup_schedule_run_now", "backup_schedule_delete",
                    "backup_history", "backup_create_now", "backup_detail", "backup_verify", "backup_download",
                    "backup_delete", "backup_toggle_protect", "backup_storage", "backup_storage_test",
                    "backup_restore_dashboard", "backup_restore_start", "backup_settings", "backup_logs",
                    "backup_export"
                ],
                "path_patterns": ["/system-admin/backups/"],
            },
        ],
    },
    {
        "code": "academic_calendar",
        "name": "Academic Calendar & Sessions",
        "category": "Academic",
        "icon": "fa-solid fa-calendar-days",
        "description": "Manage Academic Years, semester timelines, session activation, and student admission numbering schemes.",
        "is_critical": False,
        "sort_order": 3,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_academic_years", "university:admin_academic_calendar"],
        "submodules": [
            {
                "code": "academic_years",
                "name": "Academic Years & Sessions",
                "description": "Create, close, reopen, and set the current active academic session.",
                "icon": "fa-solid fa-calendar-check",
                "is_critical": False,
                "route_names": ["admin_academic_years", "admin_academic_calendar", "academic_year_create", "academic_year_detail", "academic_year_edit", "academic_year_delete", "academic_year_action"],
                "path_patterns": ["/manage/academic-years/", "/manage/academic-calendar/"],
            },
            {
                "code": "semesters",
                "name": "Semester Terms & Timelines",
                "description": "Term dates, unit registration windows, and final exam periods.",
                "icon": "fa-solid fa-clock-rotate-left",
                "is_critical": False,
                "route_names": ["semester_create", "semester_edit", "semester_delete", "semester_action"],
                "path_patterns": ["/manage/semesters/"],
            },
            {
                "code": "student_numbering",
                "name": "Student Numbering Scheme",
                "description": "Automated admission and registration number format generator.",
                "icon": "fa-solid fa-arrow-down-1-9",
                "is_critical": False,
                "route_names": ["admin_save_numbering_config", "api_numbering_preview"],
                "path_patterns": ["/manage/academic-calendar/numbering/"],
            },
        ],
    },
    {
        "code": "admissions",
        "name": "Admissions & Applications",
        "category": "Administrative",
        "icon": "fa-solid fa-id-card-clip",
        "description": "Public student applications, document uploads, application fee payment, review, and matriculation.",
        "is_critical": False,
        "sort_order": 4,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admissions_apply", "university:admin_admissions", "university:admin_admission_documents_list"],
        "submodules": [
            {
                "code": "applications",
                "name": "Application Desk & Matriculation",
                "description": "Prospective student applications, review, fee payment, and matriculation into active students.",
                "icon": "fa-solid fa-users-rectangle",
                "is_critical": False,
                "route_names": ["admissions_apply", "pay_application_fee", "admissions_status", "download_admission_letter", "admin_admissions", "admin_admission_detail", "admin_admission_matriculate"],
                "path_patterns": ["/admissions/", "/manage/admissions/"],
            },
            {
                "code": "admission_documents",
                "name": "Admission Documents & Verification",
                "description": "Issued admission letters, credential verification, resending, and revocation.",
                "icon": "fa-solid fa-folder-tree",
                "is_critical": False,
                "route_names": ["admin_admission_documents_list", "admin_admission_document_detail", "admin_regenerate_admission_document", "admin_resend_admission_document", "admin_revoke_admission_document", "admin_view_admission_document", "admin_download_admission_document", "admin_verify_attachment", "student_admission_documents", "student_view_admission_document", "student_download_admission_document", "student_download_attachment"],
                "path_patterns": ["/manage/admissions/documents/", "/me/admission-documents/"],
            },
            {
                "code": "letter_templates",
                "name": "Dynamic Letter Templates",
                "description": "Visual and ReportLab document templates with dynamic placeholder token replacement.",
                "icon": "fa-solid fa-pen-ruler",
                "is_critical": False,
                "route_names": ["admin_templates_list", "admin_template_editor", "admin_template_preview"],
                "path_patterns": ["/manage/admissions/templates/"],
            },
            {
                "code": "intakes",
                "name": "Intake Cycles",
                "description": "Cohort admission intakes linked to academic years and degree programs.",
                "icon": "fa-solid fa-calendar-plus",
                "is_critical": False,
                "route_names": ["admin_intakes"],
                "path_patterns": ["/manage/admissions/intakes/"],
            },
        ],
    },
    {
        "code": "curriculum",
        "name": "Curriculum & Programs",
        "category": "Academic",
        "icon": "fa-solid fa-graduation-cap",
        "description": "Degree programmes, academic departments, schools, and course catalog management.",
        "is_critical": False,
        "sort_order": 5,
        "target_roles": ["ADMIN", "FACULTY"],
        "route_prefixes": ["university:admin_programs", "university:admin_departments", "university:admin_courses"],
        "submodules": [
            {
                "code": "programmes",
                "name": "Degree Programmes",
                "description": "Academic programmes, credit requirements, career prospects, and award titles.",
                "icon": "fa-solid fa-graduation-cap",
                "is_critical": False,
                "route_names": ["admin_programs", "admin_programmes", "program_create", "program_export", "program_preview", "program_import", "program_import_template", "program_detail", "program_edit", "program_delete"],
                "path_patterns": ["/manage/programs/", "/manage/programmes/"],
            },
            {
                "code": "departments",
                "name": "Academic Departments & Schools",
                "description": "Faculties, schools, and academic departments.",
                "icon": "fa-solid fa-building-columns",
                "is_critical": False,
                "route_names": ["admin_departments", "department_create", "department_detail", "department_edit", "department_delete"],
                "path_patterns": ["/manage/departments/", "/departments/"],
            },
            {
                "code": "courses",
                "name": "Course Unit Catalog",
                "description": "Course units, syllabus, lecturer assignment, prerequisites, and student enrollment.",
                "icon": "fa-solid fa-book",
                "is_critical": False,
                "route_names": ["admin_courses", "course_create", "course_export", "course_preview", "course_import", "course_import_template", "course_detail", "course_edit", "course_delete", "course_enroll", "enrollment_remove", "courses_public"],
                "path_patterns": ["/courses/"],
            },
        ],
    },
    {
        "code": "students",
        "name": "Students Information System",
        "category": "Administrative",
        "icon": "fa-solid fa-user-graduate",
        "description": "Student nominal register, biodata profiles, academic status tracking, and student imports/exports.",
        "is_critical": False,
        "sort_order": 6,
        "target_roles": ["ADMIN", "FACULTY"],
        "route_prefixes": ["university:admin_students"],
        "submodules": [
            {
                "code": "student_directory",
                "name": "Student Nominal Register",
                "description": "Student biodata records, admission profiles, enrollment status, and cohort registers.",
                "icon": "fa-solid fa-user-graduate",
                "is_critical": False,
                "route_names": ["admin_students", "student_create", "student_export", "student_preview", "student_import", "student_import_template", "student_detail", "student_edit", "student_delete"],
                "path_patterns": ["/manage/students/"],
            },
        ],
    },
    {
        "code": "academics",
        "name": "Academics & Registrations",
        "category": "Academic",
        "icon": "fa-solid fa-clipboard-check",
        "description": "Course unit registration, student academic requests (deferment/leave), progressive reports, and transcripts.",
        "is_critical": False,
        "sort_order": 7,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_academics_dashboard", "university:student_register_units", "university:student_requests"],
        "submodules": [
            {
                "code": "unit_registration",
                "name": "Unit Registration",
                "description": "Online semester course unit registration and administrative approval workflows.",
                "icon": "fa-solid fa-clipboard-check",
                "is_critical": False,
                "route_names": ["student_register_units", "admin_unit_registrations", "admin_unit_registration_detail", "admin_academics_dashboard"],
                "path_patterns": ["/academics/register/", "/manage/academics/registrations/", "/manage/academics/"],
            },
            {
                "code": "student_requests",
                "name": "Student Requests & Petitions",
                "description": "Academic requests including deferment, course withdrawal, sick leave, and resumption.",
                "icon": "fa-solid fa-file-circle-question",
                "is_critical": False,
                "route_names": ["student_requests", "student_resume_studies", "admin_student_requests", "admin_student_request_detail"],
                "path_patterns": ["/academics/requests/", "/manage/academics/requests/"],
                "features": [
                    {"code": "request_deferment", "name": "Deferment Request", "action_code": "DEFERMENT", "description": "Application to postpone studies to a subsequent academic year."},
                    {"code": "request_withdrawal", "name": "Withdrawal Request", "action_code": "WITHDRAWAL", "description": "Application for official withdrawal from an academic program."},
                    {"code": "request_sick_leave", "name": "Sick Leave Application", "action_code": "SICK_LEAVE", "description": "Medical leave of absence petition."},
                    {"code": "request_resumption", "name": "Resumption of Studies", "action_code": "RESUMPTION", "description": "Application to resume active studies after approved hiatus."},
                ],
            },
            {
                "code": "transcripts",
                "name": "Transcripts & Academic Records",
                "description": "Provisional and official academic transcripts generation and verification.",
                "icon": "fa-solid fa-scroll",
                "is_critical": False,
                "route_names": ["student_provisional_transcript", "student_academic_transcript", "admin_provisional_transcripts", "admin_academic_transcripts", "transcripts", "student_transcripts", "transcript_document"],
                "path_patterns": ["/academics/provisional-transcript/", "/academics/academic-transcript/", "/manage/academics/transcripts/"],
            },
            {
                "code": "progressive_reports",
                "name": "Progressive Performance Reports",
                "description": "Year-by-year cumulative GPA, credit audit, and progressive performance sheets.",
                "icon": "fa-solid fa-chart-line",
                "is_critical": False,
                "route_names": ["student_progressive_report", "admin_progressive_reports", "progressive_report_detail", "progressive_report_export"],
                "path_patterns": ["/academics/progressive-report/", "/manage/academics/progressive-reports/"],
            },
            {
                "code": "document_controls",
                "name": "Document Release & Security Locks",
                "description": "Finance clearance and document access controls enforcing financial/academic prerequisites.",
                "icon": "fa-solid fa-lock-open",
                "is_critical": False,
                "route_names": ["admin_document_controls"],
                "path_patterns": ["/manage/academics/document-controls/"],
            },
        ],
    },
    {
        "code": "examinations",
        "name": "Examinations & Grading",
        "category": "Academic",
        "icon": "fa-solid fa-file-pen",
        "description": "Examination setup, CAT and final marks entry, moderation, Senate approvals, and results publishing.",
        "is_critical": False,
        "sort_order": 8,
        "target_roles": ["ALL"],
        "route_prefixes": ["examinations:", "university:admin_exam_nominal_rolls", "university:student_exam_card", "university:student_supplementary"],
        "submodules": [
            {
                "code": "exam_setup",
                "name": "Exam Setup & Scheduling",
                "description": "Exam timetable scheduling, room allocation, and grading scheme definitions.",
                "icon": "fa-solid fa-sliders",
                "is_critical": False,
                "route_names": ["exam_create", "exam_edit", "exam_delete", "index", "create", "rooms", "room_edit", "terms", "term_edit", "grading", "grading_edit", "report", "admission"],
                "path_patterns": ["/manage/academics/examinations/"],
            },
            {
                "code": "marks_entry",
                "name": "Continuous Assessment & Exam Marks",
                "description": "Submission and moderation of continuous assessment (CAT 30%) and final examination (70%) marks.",
                "icon": "fa-solid fa-pen-nib",
                "is_critical": False,
                "route_names": ["marks", "register", "faculty_grade"],
                "path_patterns": ["/teach/grade/"],
                "features": [
                    {"code": "cat_marks", "name": "Enter CAT / Coursework Marks", "action_code": "CAT_ENTRY", "description": "Continuous assessment marks entry (30%)."},
                    {"code": "exam_marks", "name": "Enter Final Written Exam Marks", "action_code": "EXAM_ENTRY", "description": "Final written exam marks entry (70%)."},
                    {"code": "marks_moderation", "name": "Marks Moderation & Adjustment", "action_code": "MODERATION", "description": "Departmental moderation and grade curve review."},
                ],
            },
            {
                "code": "results_approval",
                "name": "Results & Senate Approvals",
                "description": "Official results publishing, student statements of results, and grade appeals.",
                "icon": "fa-solid fa-award",
                "is_critical": False,
                "route_names": ["statement", "student_statement", "student_results", "appeal", "resolve"],
                "path_patterns": ["/me/results/"],
                "features": [
                    {"code": "publish_results", "name": "Publish Results to Portal", "action_code": "PUBLISH_RESULTS", "description": "Release Senate-approved results to students."},
                    {"code": "grade_appeals", "name": "Grade Re-check & Appeals", "action_code": "GRADE_APPEALS", "description": "Student grade appeal submission and resolution."},
                ],
            },
            {
                "code": "nominal_rolls",
                "name": "Examination Nominal Rolls",
                "description": "Attendance registers and examination seating rolls.",
                "icon": "fa-solid fa-users-viewfinder",
                "is_critical": False,
                "route_names": ["admin_exam_nominal_rolls", "admin_exam_nominal_roll_pdf"],
                "path_patterns": ["/manage/academics/nominal-rolls/"],
            },
            {
                "code": "exam_cards",
                "name": "Student Exam Cards",
                "description": "Student examination clearance cards with photo verification and unit schedule.",
                "icon": "fa-solid fa-id-card",
                "is_critical": False,
                "route_names": ["student_exam_card", "student_exam_card_pdf"],
                "path_patterns": ["/academics/exam-card/"],
            },
            {
                "code": "supplementary_exams",
                "name": "Supplementary & Special Exams",
                "description": "Supplementary exam applications, approvals, and scheduling for failed units.",
                "icon": "fa-solid fa-arrows-rotate",
                "is_critical": False,
                "route_names": ["student_supplementary", "student_supplementary_apply", "admin_supplementary_list", "admin_supplementary_decision"],
                "path_patterns": ["/academics/supplementary/", "/manage/academics/supplementary/"],
            },
        ],
    },
    {
        "code": "finance",
        "name": "Finance & Fees",
        "category": "Finance",
        "icon": "fa-solid fa-wallet",
        "description": "Tuition fee structures, student invoices, fee receipts, payment reconciliation, and statements.",
        "is_critical": False,
        "sort_order": 9,
        "target_roles": ["ALL"],
        "route_prefixes": [
            "university:admin_fees",
            "university:student_fees",
            "university:student_pay_fees",
            "university:student_fee_statement",
            "university:fee_accounts_dashboard",
            "university:admin_payments_list",
            "university:fee_reconciliation_dashboard",
        ],
        "submodules": [
            {
                "code": "pay_fees",
                "name": "Student Pay Fees",
                "description": "Online student payment interface integrating active institutional channels.",
                "icon": "fa-solid fa-money-bill-wave",
                "is_critical": False,
                "route_names": ["student_pay_fees", "student_initiate_payment", "student_payment_status", "student_payment_poll", "student_receipt_view", "student_receipt_pdf"],
                "path_patterns": ["/me/fees/pay/"],
            },
            {
                "code": "fee_accounts",
                "name": "Fee Accounts & Payment Gateways",
                "description": "Configure M-Pesa Paybill, Till, Card Gateways, Bank accounts, and credentials.",
                "icon": "fa-solid fa-building-columns",
                "is_critical": False,
                "route_names": ["fee_accounts_dashboard", "fee_account_create", "fee_account_detail", "fee_account_edit", "fee_account_toggle_status", "fee_account_set_default", "fee_account_test", "fee_account_delete"],
                "path_patterns": ["/finance/fee-accounts/"],
            },
            {
                "code": "payments_ledger",
                "name": "Payments Ledger & Settlements",
                "description": "Verify, inspect, reverse, and refund student fee transactions.",
                "icon": "fa-solid fa-receipt",
                "is_critical": False,
                "route_names": ["admin_payments_list", "admin_payment_detail", "admin_payment_verify", "admin_payment_reverse"],
                "path_patterns": ["/manage/fees/payments/"],
            },
            {
                "code": "fee_reconciliation",
                "name": "Payment Reconciliation",
                "description": "Provider statement transaction matching and exception resolution.",
                "icon": "fa-solid fa-scale-balanced",
                "is_critical": False,
                "route_names": ["fee_reconciliation_dashboard", "fee_reconciliation_match"],
                "path_patterns": ["/finance/fee-accounts/reconciliation/"],
            },
            {
                "code": "fee_invoices",
                "name": "Fee Invoices & Receipts",
                "description": "Issue tuition invoices, record cash/bank receipts, and print official receipts.",
                "icon": "fa-solid fa-file-invoice-dollar",
                "is_critical": False,
                "route_names": ["admin_fees", "fee_export", "fee_preview", "fee_create", "record_payment", "fee_receipt_pdf", "student_fees"],
                "path_patterns": ["/manage/fees/", "/me/fees/"],
            },
            {
                "code": "fee_structures",
                "name": "Programme Fee Structures",
                "description": "Fee schedules categorized by programme, academic year, and semester.",
                "icon": "fa-solid fa-scale-balanced",
                "is_critical": False,
                "route_names": ["admin_fee_structures", "fee_structure_create", "fee_structure_edit", "fee_structure_delete"],
                "path_patterns": ["/manage/fees/structures/"],
            },
            {
                "code": "fee_statements",
                "name": "Student Statements of Account",
                "description": "Detailed student ledger statements with debits, credits, and outstanding balances.",
                "icon": "fa-solid fa-file-lines",
                "is_critical": False,
                "route_names": ["student_fee_statement", "student_fee_statement_pdf"],
                "path_patterns": ["/me/fees/statement/"],
            },
        ],
    },
    {
        "code": "timetable",
        "name": "Timetable & Scheduling",
        "category": "Academic",
        "icon": "fa-solid fa-calendar-week",
        "description": "Lecture room scheduling, timetable publishing, conflict checking, and faculty/student class views.",
        "is_critical": False,
        "sort_order": 10,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_timetable", "university:faculty_timetable", "university:student_timetable"],
        "submodules": [
            {
                "code": "schedule_mgmt",
                "name": "Timetable Management",
                "description": "Master class scheduling, room assignments, time slots, and timetable publishing.",
                "icon": "fa-solid fa-calendar-week",
                "is_critical": False,
                "route_names": ["admin_timetable", "class_schedule_create", "timetable_export", "timetable_preview", "timetable_import", "timetable_import_template", "class_schedule_edit", "class_schedule_delete", "class_schedule_publish", "faculty_timetable", "student_timetable"],
                "path_patterns": ["/manage/timetable/", "/teach/timetable/", "/me/timetable/"],
            },
        ],
    },
    {
        "code": "faculty_ops",
        "name": "Faculty Operations & Teaching",
        "category": "Academic",
        "icon": "fa-solid fa-chalkboard-user",
        "description": "Lecturer profile management, assigned course units, attendance registers, and assignments.",
        "is_critical": False,
        "sort_order": 11,
        "target_roles": ["ADMIN", "FACULTY"],
        "route_prefixes": ["university:admin_faculty", "university:faculty_courses", "university:faculty_assignments"],
        "submodules": [
            {
                "code": "faculty_directory",
                "name": "Faculty Staff Directory",
                "description": "Academic staff directory, departmental appointments, and contact profiles.",
                "icon": "fa-solid fa-chalkboard-user",
                "is_critical": False,
                "route_names": ["admin_faculty", "faculty_create", "faculty_export", "faculty_preview", "faculty_import", "faculty_import_template", "faculty_detail", "faculty_edit", "faculty_delete"],
                "path_patterns": ["/manage/faculty/"],
            },
            {
                "code": "my_teaching",
                "name": "Course Allocation & Teaching",
                "description": "Lecturer course dashboards and enrolled student registers.",
                "icon": "fa-solid fa-book-open",
                "is_critical": False,
                "route_names": ["faculty_courses", "student_courses"],
                "path_patterns": ["/teach/courses/", "/me/courses/"],
            },
            {
                "code": "attendance",
                "name": "Lecture Attendance Tracking",
                "description": "Daily lecture attendance capture and student percentage compliance tracking.",
                "icon": "fa-solid fa-calendar-check",
                "is_critical": False,
                "route_names": ["faculty_attendance", "faculty_attendance_history", "student_attendance"],
                "path_patterns": ["/teach/attendance/", "/me/attendance/"],
            },
            {
                "code": "assignments",
                "name": "Assignments & Coursework",
                "description": "Create coursework assignments, accept student submissions, and award grades.",
                "icon": "fa-solid fa-list-check",
                "is_critical": False,
                "route_names": ["faculty_assignments", "assignment_create", "assignment_edit", "assignment_delete", "assignment_detail", "student_assignments", "submit_assignment"],
                "path_patterns": ["/teach/assignments/", "/me/assignments/", "/assignments/"],
            },
        ],
    },
    {
        "code": "campus_services",
        "name": "Campus Services & Hostels",
        "category": "Student Services",
        "icon": "fa-solid fa-hotel",
        "description": "Campus residential accommodation, hostel room allocation, check-in, and check-out management.",
        "is_critical": False,
        "sort_order": 12,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_hostels_dashboard", "university:student_hostel_portal"],
        "submodules": [
            {
                "code": "hostels",
                "name": "Hostels & Accommodation",
                "description": "Hostel room inventory, online room applications, bed allocation, and check-in/out.",
                "icon": "fa-solid fa-bed",
                "is_critical": False,
                "route_names": ["student_hostel_portal", "student_hostel_apply", "admin_hostels_dashboard", "admin_hostel_allocate", "admin_hostel_checkin", "admin_hostel_checkout"],
                "path_patterns": ["/campus/hostels/", "/manage/hostels/"],
            },
        ],
    },
    {
        "code": "library",
        "name": "Library & Past Papers",
        "category": "Student Services",
        "icon": "fa-solid fa-book-bookmark",
        "description": "Library book catalog, circulation loans, returns, and digital past examination paper repository.",
        "is_critical": False,
        "sort_order": 13,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_library_dashboard", "university:student_library_portal"],
        "submodules": [
            {
                "code": "library_catalog",
                "name": "Library Circulation & Books",
                "description": "Book inventory, borrowing issues, and overdue returns tracking.",
                "icon": "fa-solid fa-book-bookmark",
                "is_critical": False,
                "route_names": ["student_library_portal", "admin_library_dashboard", "admin_library_issue", "admin_library_return", "admin_library_book_create"],
                "path_patterns": ["/campus/library/", "/manage/library/"],
            },
            {
                "code": "past_papers",
                "name": "Past Examination Papers",
                "description": "Digital archive of past exam question papers for student revision.",
                "icon": "fa-solid fa-file-lines",
                "is_critical": False,
                "route_names": ["admin_past_paper_create"],
                "path_patterns": ["/manage/library/past-papers/"],
            },
        ],
    },
    {
        "code": "industrial_attachment",
        "name": "Industrial Attachment & Practicum",
        "category": "Student Services",
        "icon": "fa-solid fa-briefcase",
        "description": "Practicum placement applications, weekly digital logbooks, faculty supervisor visits, and grading.",
        "is_critical": False,
        "sort_order": 14,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:student_attachment_portal", "university:faculty_attachment_dashboard", "university:admin_attachment_dashboard"],
        "submodules": [
            {
                "code": "attachment_portal",
                "name": "Practicum Management & Supervision",
                "description": "Student placement letters, weekly logbooks, faculty assessor visits, and final grading.",
                "icon": "fa-solid fa-briefcase",
                "is_critical": False,
                "route_names": ["student_attachment_portal", "student_attachment_apply", "student_attachment_logbook_submit", "attachment_intro_letter_pdf", "attachment_logbook_pdf", "faculty_attachment_dashboard", "faculty_attachment_review_log", "faculty_attachment_grade", "admin_attachment_dashboard", "admin_attachment_action"],
                "path_patterns": ["/academics/attachment/", "/faculty/attachments/", "/manage/academics/attachments/"],
            },
        ],
    },
    {
        "code": "graduation",
        "name": "Graduation & Clearance",
        "category": "Student Services",
        "icon": "fa-solid fa-award",
        "description": "Multi-department exit clearance (Library, Finance, HOD, Dean, Registrar), ceremony lists, and certificates.",
        "is_critical": False,
        "sort_order": 15,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:student_graduation", "university:admin_graduation_dashboard"],
        "submodules": [
            {
                "code": "graduation_clearance",
                "name": "Graduation Clearance & Gowns",
                "description": "Clearance queue management, Senate graduation approval, and degree certificates.",
                "icon": "fa-solid fa-award",
                "is_critical": False,
                "route_names": ["student_graduation", "student_apply_clearance", "student_degree_certificate_pdf", "student_clearance_certificate_pdf", "admin_graduation_dashboard", "admin_ceremony_create", "admin_clearance_queue", "admin_clearance_action", "admin_senate_approve"],
                "path_patterns": ["/academics/graduation/", "/manage/graduation/"],
            },
        ],
    },
    {
        "code": "evaluations",
        "name": "Course & Lecturer Evaluations",
        "category": "Academic",
        "icon": "fa-solid fa-star-half-stroke",
        "description": "Quality assurance student feedback surveys, rating metrics, evaluation windows, and Dean analytics.",
        "is_critical": False,
        "sort_order": 16,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:student_evaluation_portal", "university:faculty_evaluation_dashboard", "university:admin_evaluation_dashboard"],
        "submodules": [
            {
                "code": "evaluations_portal",
                "name": "Course & Lecturer Feedback",
                "description": "Anonymous student surveys, quality assurance reports, and teaching scorecards.",
                "icon": "fa-solid fa-star-half-stroke",
                "is_critical": False,
                "route_names": ["student_evaluation_portal", "student_evaluation_submit", "faculty_evaluation_dashboard", "admin_evaluation_dashboard", "admin_evaluation_course_detail", "admin_evaluation_window_toggle", "admin_evaluation_export"],
                "path_patterns": ["/me/evaluation/", "/teach/evaluation/", "/manage/evaluation/"],
            },
        ],
    },
    {
        "code": "reports",
        "name": "University Reporting Hub",
        "category": "Administrative",
        "icon": "fa-solid fa-chart-pie",
        "description": "Centralized analytical reports, Senate consolidated marksheets, demographic charts, and Excel/PDF exports.",
        "is_critical": False,
        "sort_order": 17,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:admin_reports_dashboard", "university:faculty_reports", "university:student_reports"],
        "submodules": [
            {
                "code": "reporting_hub",
                "name": "Institutional Reports & Exports",
                "description": "Interactive reporting catalog, filtering, and export to PDF, Excel, and CSV.",
                "icon": "fa-solid fa-chart-pie",
                "is_critical": False,
                "route_names": ["admin_reports_dashboard", "report_view", "export_report", "faculty_reports", "student_reports"],
                "path_patterns": ["/manage/reports/", "/teach/reports/", "/me/reports/"],
            },
        ],
    },
    {
        "code": "communication",
        "name": "Campus Notices & Events",
        "category": "Administrative",
        "icon": "fa-solid fa-bullhorn",
        "description": "University announcements, audience-targeted notices, and campus events calendar.",
        "is_critical": False,
        "sort_order": 18,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:notices", "university:events"],
        "submodules": [
            {
                "code": "notices_events",
                "name": "Notices & University Events",
                "description": "Campus announcements, audience filters, and upcoming events calendar.",
                "icon": "fa-solid fa-bullhorn",
                "is_critical": False,
                "route_names": ["notices", "notice_delete", "events", "event_create", "event_edit", "event_delete"],
                "path_patterns": ["/notices/", "/events/"],
            },
        ],
    },
    {
        "code": "ai_assistant",
        "name": "AI Assistant & Insights",
        "category": "Intelligence",
        "icon": "fa-solid fa-wand-magic-sparkles",
        "description": "Generative AI conversational assistant, automated academic summaries, and predictive student risk alerts.",
        "is_critical": False,
        "sort_order": 19,
        "target_roles": ["ALL"],
        "route_prefixes": ["university:ai_assistant", "university:ai_insights"],
        "submodules": [
            {
                "code": "ai_suite",
                "name": "AI Intelligence & Analytics",
                "description": "Conversational assistant and real-time academic performance insights.",
                "icon": "fa-solid fa-robot",
                "is_critical": False,
                "route_names": ["ai_assistant", "ai_reply", "ai_insights"],
                "path_patterns": ["/ai/assistant/", "/ai/insights/"],
            },
        ],
    },
    {
        "code": "cms",
        "name": "Public Website CMS",
        "category": "Public & CMS",
        "icon": "fa-solid fa-pen-ruler",
        "description": "Public university website builder, page blocks, menu editor, and media library.",
        "is_critical": False,
        "sort_order": 20,
        "target_roles": ["ADMIN"],
        "route_prefixes": ["cms:"],
        "submodules": [
            {
                "code": "cms_manager",
                "name": "Site Pages & Content Builder",
                "description": "Visual page editing, modular content blocks, dynamic navigation menus, and media.",
                "icon": "fa-solid fa-pen-ruler",
                "is_critical": False,
                "route_names": ["dashboard", "settings", "page_list", "page_create", "page_edit", "page_delete", "page_status", "block_create", "block_edit", "block_delete", "block_move", "menu_list", "menu_create", "menu_edit", "menu_delete", "media", "media_delete"],
                "path_patterns": ["/manage/site/"],
            },
        ],
    },
]

# Standard cross-module dependencies
DEFAULT_DEPENDENCIES = [
    # (source_module_code, target_module_code, description)
    ("academics", "academic_calendar", "Academics & Unit Registration require Academic Calendar to be active."),
    ("examinations", "academic_calendar", "Examinations and marks require an active Academic Calendar session."),
    ("examinations", "curriculum", "Examinations require Course Catalog & Curriculum to be active."),
    ("graduation", "academics", "Graduation clearance requires Academic Records to be active."),
    ("graduation", "finance", "Graduation clearance requires Finance for fee balance checks."),
    ("industrial_attachment", "students", "Industrial Attachment requires Student Records to be active."),
    ("finance", "students", "Finance & Invoicing requires Student Directory to be active."),
]


# ==============================================================================
# 2. SEEDING & INITIALIZATION ENGINE
# ==============================================================================

@transaction.atomic
def seed_system_modules():
    """
    Idempotently seeds all system modules, submodules, features, and dependencies.
    Preserves any administrative status customizations on existing records.
    """
    modules_created = 0
    submodules_created = 0
    features_created = 0

    for m_data in SYSTEM_MODULES_CATALOG:
        mod_obj, created = SystemModule.objects.get_or_create(
            code=m_data["code"],
            defaults={
                "name": m_data["name"],
                "category": m_data["category"],
                "description": m_data["description"],
                "icon": m_data["icon"],
                "status": ModuleStatus.ENABLED,
                "is_critical": m_data["is_critical"],
                "sort_order": m_data["sort_order"],
                "target_roles": m_data["target_roles"],
                "route_prefixes": m_data["route_prefixes"],
            }
        )
        if created:
            modules_created += 1
        else:
            # Update metadata without overwriting status or status_message
            mod_obj.name = m_data["name"]
            mod_obj.category = m_data["category"]
            mod_obj.description = m_data["description"]
            mod_obj.icon = m_data["icon"]
            mod_obj.is_critical = m_data["is_critical"]
            mod_obj.sort_order = m_data["sort_order"]
            mod_obj.target_roles = m_data["target_roles"]
            mod_obj.route_prefixes = m_data["route_prefixes"]
            mod_obj.save()

        # Seed submodules
        for sub_data in m_data.get("submodules", []):
            sub_obj, s_created = SystemSubmodule.objects.get_or_create(
                code=sub_data["code"],
                defaults={
                    "module": mod_obj,
                    "name": sub_data["name"],
                    "description": sub_data.get("description", ""),
                    "icon": sub_data.get("icon", "fa-solid fa-circle-dot"),
                    "status": ModuleStatus.ENABLED,
                    "is_critical": sub_data.get("is_critical", False),
                    "route_names": sub_data.get("route_names", []),
                    "path_patterns": sub_data.get("path_patterns", []),
                }
            )
            if s_created:
                submodules_created += 1
            else:
                sub_obj.module = mod_obj
                sub_obj.name = sub_data["name"]
                sub_obj.description = sub_data.get("description", "")
                sub_obj.icon = sub_data.get("icon", "fa-solid fa-circle-dot")
                sub_obj.is_critical = sub_data.get("is_critical", False)
                sub_obj.route_names = sub_data.get("route_names", [])
                sub_obj.path_patterns = sub_data.get("path_patterns", [])
                sub_obj.save()

            # Seed features if defined
            for feat_data in sub_data.get("features", []):
                feat_obj, f_created = SystemFeature.objects.get_or_create(
                    code=feat_data["code"],
                    defaults={
                        "submodule": sub_obj,
                        "name": feat_data["name"],
                        "description": feat_data.get("description", ""),
                        "status": ModuleStatus.ENABLED,
                        "is_critical": feat_data.get("is_critical", False),
                        "action_code": feat_data.get("action_code", ""),
                        "route_names": feat_data.get("route_names", []),
                    }
                )
                if f_created:
                    features_created += 1
                else:
                    feat_obj.submodule = sub_obj
                    feat_obj.name = feat_data["name"]
                    feat_obj.description = feat_data.get("description", "")
                    feat_obj.is_critical = feat_data.get("is_critical", False)
                    feat_obj.action_code = feat_data.get("action_code", "")
                    feat_obj.route_names = feat_data.get("route_names", [])
                    feat_obj.save()

    # Seed default dependencies
    for src_code, tgt_code, desc in DEFAULT_DEPENDENCIES:
        src = SystemModule.objects.filter(code=src_code).first()
        tgt = SystemModule.objects.filter(code=tgt_code).first()
        if src and tgt:
            ModuleDependency.objects.get_or_create(
                source_module=src,
                target_module=tgt,
                defaults={"description": desc, "dependency_type": ModuleDependency.DependencyType.REQUIRED}
            )

    invalidate_module_cache()
    return modules_created, submodules_created, features_created


# ==============================================================================
# 3. HIGH-SPEED REGISTRY CACHE
# ==============================================================================

def get_cached_module_registry():
    """
    Returns an optimized snapshot of the entire module hierarchy from cache.
    Rebuilds from database if cache is empty or invalidated.
    """
    data = cache.get(CACHE_KEY_MODULE_REGISTRY)
    if data is not None:
        return data

    # Rebuild from database
    if SystemModule.objects.count() == 0:
        seed_system_modules()

    modules_by_code = {}
    active_module_codes = set()
    submodules_by_code = {}
    active_submodule_codes = set()
    features_by_code = {}
    active_feature_codes = set()
    feature_by_action = {}
    route_to_submodule = {}
    path_to_submodule = []

    modules = SystemModule.objects.prefetch_related(
        "submodules", "submodules__features", "dependencies_as_source__target_module"
    ).all()

    for mod in modules:
        is_mod_active = (mod.status == ModuleStatus.ENABLED)
        if is_mod_active:
            active_module_codes.add(mod.code)

        mod_data = {
            "id": mod.id,
            "code": mod.code,
            "name": mod.name,
            "category": mod.category,
            "icon": mod.icon,
            "status": mod.status,
            "status_message": mod.status_message,
            "is_critical": mod.is_critical,
            "is_active": is_mod_active,
            "target_roles": mod.target_roles,
            "route_prefixes": mod.route_prefixes,
        }
        modules_by_code[mod.code] = mod_data

        for sub in mod.submodules.all():
            is_sub_active = is_mod_active and (sub.status == ModuleStatus.ENABLED)
            if is_sub_active:
                active_submodule_codes.add(sub.code)

            sub_data = {
                "id": sub.id,
                "module_code": mod.code,
                "module_name": mod.name,
                "module_status": mod.status,
                "code": sub.code,
                "name": sub.name,
                "icon": sub.icon,
                "status": sub.status,
                "effective_status": mod.status if not is_mod_active else sub.status,
                "status_message": sub.status_message or mod.status_message,
                "is_critical": sub.is_critical or mod.is_critical,
                "is_active": is_sub_active,
                "route_names": sub.route_names,
                "path_patterns": sub.path_patterns,
            }
            submodules_by_code[sub.code] = sub_data

            # Map route names
            for rname in sub.route_names:
                route_to_submodule[rname] = sub_data
                # Also without namespace prefix if any
                if ":" in rname:
                    route_to_submodule[rname.split(":")[-1]] = sub_data

            # Map path patterns
            for ppattern in sub.path_patterns:
                path_to_submodule.append((ppattern, sub_data))

            for feat in sub.features.all():
                is_feat_active = is_sub_active and (feat.status == ModuleStatus.ENABLED)
                if is_feat_active:
                    active_feature_codes.add(feat.code)

                feat_data = {
                    "id": feat.id,
                    "submodule_code": sub.code,
                    "module_code": mod.code,
                    "code": feat.code,
                    "name": feat.name,
                    "action_code": feat.action_code,
                    "status": feat.status,
                    "effective_status": sub_data["effective_status"] if not is_sub_active else feat.status,
                    "status_message": feat.status_message or sub_data["status_message"],
                    "is_active": is_feat_active,
                }
                features_by_code[feat.code] = feat_data
                if feat.action_code:
                    feature_by_action[feat.action_code] = feat_data

    # Map dependencies
    dependencies = []
    for dep in ModuleDependency.objects.select_related("source_module", "target_module").all():
        dependencies.append({
            "source_code": dep.source_module.code,
            "source_name": dep.source_module.name,
            "target_code": dep.target_module.code,
            "target_name": dep.target_module.name,
            "dependency_type": dep.dependency_type,
            "description": dep.description,
        })

    registry = {
        "modules_by_code": modules_by_code,
        "active_module_codes": active_module_codes,
        "submodules_by_code": submodules_by_code,
        "active_submodule_codes": active_submodule_codes,
        "features_by_code": features_by_code,
        "active_feature_codes": active_feature_codes,
        "feature_by_action": feature_by_action,
        "route_to_submodule": route_to_submodule,
        "path_to_submodule": path_to_submodule,
        "dependencies": dependencies,
        "cached_at": timezone.now().isoformat(),
    }

    cache.set(CACHE_KEY_MODULE_REGISTRY, registry, CACHE_TIMEOUT_SECONDS)
    return registry


def invalidate_module_cache():
    """Clears cached registry state across all workers immediately."""
    cache.delete(CACHE_KEY_MODULE_REGISTRY)


# ==============================================================================
# 4. DEPENDENCY CHECKING & RESOLUTION
# ==============================================================================

def check_module_dependencies(module_code, new_status):
    """
    Evaluates whether disabling a module violates required dependencies of other ACTIVE modules.
    Returns: (is_allowed: bool, conflicts: list[str])
    """
    if new_status == ModuleStatus.ENABLED:
        return True, []

    # If attempting to disable or set to maintenance, check if any active modules require it
    conflicts = []
    dependents = ModuleDependency.objects.filter(
        target_module__code=module_code,
        dependency_type=ModuleDependency.DependencyType.REQUIRED
    ).select_related("source_module")

    for dep in dependents:
        src = dep.source_module
        if src.status == ModuleStatus.ENABLED and src.code != module_code:
            conflicts.append({
                "module_code": src.code,
                "module_name": src.name,
                "description": dep.description or f"{src.name} requires {dep.target_module.name}.",
            })

    if conflicts:
        return False, conflicts
    return True, []


# ==============================================================================
# 5. SAFE STATUS TRANSITIONS & AUDIT LOGGING
# ==============================================================================

@transaction.atomic
def set_module_status(module_code, new_status, status_message="", user=None, bypass_dependencies=False):
    """
    Safely modifies the operational status of a SystemModule (Level 1).
    Enforces critical module protections, dependency checks, and audit logging.
    """
    mod = SystemModule.objects.select_for_update().filter(code=module_code).first()
    if not mod:
        return False, f"Module '{module_code}' not found."

    if mod.is_critical and new_status != ModuleStatus.ENABLED:
        return False, f"'{mod.name}' is a critical system module and cannot be disabled."

    if not bypass_dependencies and new_status != ModuleStatus.ENABLED:
        allowed, conflicts = check_module_dependencies(module_code, new_status)
        if not allowed:
            conflict_names = ", ".join([c["module_name"] for c in conflicts])
            return False, f"Cannot disable '{mod.name}' because {conflict_names} depend on it."

    old_status = mod.status
    mod.status = new_status
    if status_message is not None:
        mod.status_message = status_message
    if user and user.is_authenticated:
        mod.updated_by = user
    mod.save()

    # Log forensic audit trail
    action = AuditLog.Action.MODULE_STATUS_CHANGE
    if new_status == ModuleStatus.ENABLED:
        action = AuditLog.Action.MODULE_ENABLE
    elif new_status == ModuleStatus.DISABLED:
        action = AuditLog.Action.MODULE_DISABLE

    log_activity(
        action=action,
        module=AuditLog.Module.MODULE_MGMT,
        description=f"Module '{mod.name}' status changed from '{old_status}' to '{new_status}'. Reason: {status_message or 'No comment'}",
        user=user,
        entity="SystemModule",
        entity_id=str(mod.id),
        new_state={"code": mod.code, "status": mod.status, "status_message": mod.status_message},
        previous_state={"code": mod.code, "status": old_status},
    )

    invalidate_module_cache()
    return True, f"Module '{mod.name}' status updated to {mod.get_status_display()}."


@transaction.atomic
def set_submodule_status(submodule_code, new_status, status_message="", user=None):
    """
    Modifies status of a specific SystemSubmodule (Level 2).
    """
    sub = SystemSubmodule.objects.select_for_update().filter(code=submodule_code).first()
    if not sub:
        return False, f"Submodule '{submodule_code}' not found."

    if sub.is_critical and new_status != ModuleStatus.ENABLED:
        return False, f"'{sub.name}' is a critical core submodule and cannot be disabled."

    old_status = sub.status
    sub.status = new_status
    if status_message is not None:
        sub.status_message = status_message
    if user and user.is_authenticated:
        sub.updated_by = user
    sub.save()

    log_activity(
        action=AuditLog.Action.SUBMODULE_STATUS_CHANGE,
        module=AuditLog.Module.MODULE_MGMT,
        description=f"Submodule '{sub.module.name} → {sub.name}' status changed from '{old_status}' to '{new_status}'.",
        user=user,
        entity="SystemSubmodule",
        entity_id=str(sub.id),
        new_state={"code": sub.code, "status": sub.status, "status_message": sub.status_message},
        previous_state={"code": sub.code, "status": old_status},
    )

    invalidate_module_cache()
    return True, f"Submodule '{sub.name}' status updated to {sub.get_status_display()}."


@transaction.atomic
def set_feature_status(feature_code, new_status, status_message="", user=None):
    """
    Modifies status of a granular SystemFeature (Level 3).
    """
    feat = SystemFeature.objects.select_for_update().filter(code=feature_code).first()
    if not feat:
        return False, f"Feature '{feature_code}' not found."

    if feat.is_critical and new_status != ModuleStatus.ENABLED:
        return False, f"'{feat.name}' is a critical system feature and cannot be disabled."

    old_status = feat.status
    feat.status = new_status
    if status_message is not None:
        feat.status_message = status_message
    if user and user.is_authenticated:
        feat.updated_by = user
    feat.save()

    log_activity(
        action=AuditLog.Action.FEATURE_STATUS_CHANGE,
        module=AuditLog.Module.MODULE_MGMT,
        description=f"Feature '{feat.submodule.module.name} → {feat.submodule.name} → {feat.name}' status changed to '{new_status}'.",
        user=user,
        entity="SystemFeature",
        entity_id=str(feat.id),
        new_state={"code": feat.code, "status": feat.status},
        previous_state={"code": feat.code, "status": old_status},
    )

    invalidate_module_cache()
    return True, f"Feature '{feat.name}' status updated to {feat.get_status_display()}."


@transaction.atomic
def bulk_set_modules_status(module_codes, new_status, user=None, status_message=""):
    """
    Bulk updates multiple modules safely while protecting critical system modules.
    """
    updated_count = 0
    skipped_critical = []
    skipped_dependencies = []

    for code in module_codes:
        mod = SystemModule.objects.filter(code=code).first()
        if not mod:
            continue

        if mod.is_critical and new_status != ModuleStatus.ENABLED:
            skipped_critical.append(mod.name)
            continue

        if new_status != ModuleStatus.ENABLED:
            allowed, conflicts = check_module_dependencies(code, new_status)
            if not allowed:
                skipped_dependencies.append((mod.name, [c["module_name"] for c in conflicts]))
                continue

        old_status = mod.status
        mod.status = new_status
        if status_message:
            mod.status_message = status_message
        if user and user.is_authenticated:
            mod.updated_by = user
        mod.save()
        updated_count += 1

    log_activity(
        action=AuditLog.Action.MODULES_BULK_UPDATE,
        module=AuditLog.Module.MODULE_MGMT,
        description=f"Bulk updated {updated_count} modules to '{new_status}'.",
        user=user,
        new_state={"target_status": new_status, "count": updated_count},
    )

    invalidate_module_cache()
    msg = f"Successfully updated {updated_count} modules to {new_status}."
    if skipped_critical:
        msg += f" (Protected {len(skipped_critical)} critical modules: {', '.join(skipped_critical)})."
    if skipped_dependencies:
        dep_names = ", ".join([f"{item[0]} (dep by {','.join(item[1])})" for item in skipped_dependencies])
        msg += f" (Skipped due to dependencies: {dep_names})."

    return True, msg


@transaction.atomic
def enable_all_modules(user=None):
    """Enables all modules and their submodules across the university."""
    modules = SystemModule.objects.all()
    count = 0
    for mod in modules:
        mod.status = ModuleStatus.ENABLED
        if user and user.is_authenticated:
            mod.updated_by = user
        mod.save()
        mod.submodules.all().update(status=ModuleStatus.ENABLED)
        SystemFeature.objects.filter(submodule__module=mod).update(status=ModuleStatus.ENABLED)
        count += 1

    log_activity(
        action=AuditLog.Action.MODULE_ENABLE,
        module=AuditLog.Module.MODULE_MGMT,
        description="Admin executed 'Enable All' on all university modules, submodules, and features.",
        user=user,
    )
    invalidate_module_cache()
    return True, f"All {count} university modules and submodules are now active."


@transaction.atomic
def disable_all_configurable_modules(user=None, status_message="System undergoing institutional maintenance."):
    """
    Safely disables all configurable modules while strictly protecting critical modules.
    """
    modules = SystemModule.objects.filter(is_critical=False)
    count = 0
    for mod in modules:
        mod.status = ModuleStatus.DISABLED
        mod.status_message = status_message
        if user and user.is_authenticated:
            mod.updated_by = user
        mod.save()
        count += 1

    log_activity(
        action=AuditLog.Action.MODULE_DISABLE,
        module=AuditLog.Module.MODULE_MGMT,
        description=f"Admin executed 'Disable All' on {count} configurable modules (Critical system modules remained active).",
        user=user,
    )
    invalidate_module_cache()
    return True, f"Disabled {count} configurable modules. Critical system modules remain active."


# ==============================================================================
# 6. ROUTE & PERMISSION RESOLVER HELPERS
# ==============================================================================

def is_module_active(module_code):
    """Fast check whether a module is active."""
    registry = get_cached_module_registry()
    return module_code in registry["active_module_codes"]


def is_submodule_active(submodule_code):
    """Fast check whether a submodule is active."""
    registry = get_cached_module_registry()
    return submodule_code in registry["active_submodule_codes"]


def is_feature_active(feature_code_or_action):
    """Fast check whether a feature is active by feature code or action code."""
    registry = get_cached_module_registry()
    if feature_code_or_action in registry["active_feature_codes"]:
        return True
    if feature_code_or_action in registry["feature_by_action"]:
        return registry["feature_by_action"][feature_code_or_action]["is_active"]
    return True  # If feature not tracked specifically, default to allowed


def get_request_module_context(request):
    """
    Inspects an incoming request and determines if it belongs to a module/submodule.
    Returns: (is_managed: bool, is_accessible: bool, context_info: dict)
    """
    registry = get_cached_module_registry()
    resolver_match = getattr(request, "resolver_match", None)
    url_name = resolver_match.url_name if resolver_match else ""
    namespace = resolver_match.namespace if resolver_match else ""
    path = request.path

    # Check by route name
    sub_data = registry["route_to_submodule"].get(url_name)
    if not sub_data and namespace:
        full_route = f"{namespace}:{url_name}"
        sub_data = registry["route_to_submodule"].get(full_route)

    # Check by path prefixes if not matched by route name
    if not sub_data:
        for ppattern, s_data in registry["path_to_submodule"]:
            if path.startswith(ppattern):
                sub_data = s_data
                break

    if not sub_data:
        return False, True, {}

    # Module is managed!
    mod_code = sub_data["module_code"]
    mod_data = registry["modules_by_code"].get(mod_code, {})

    is_active = sub_data["is_active"]
    effective_status = sub_data["effective_status"]
    status_message = sub_data["status_message"] or mod_data.get("status_message", "")

    context_info = {
        "module_code": mod_code,
        "module_name": mod_data.get("name", ""),
        "module_icon": mod_data.get("icon", "fa-solid fa-cube"),
        "submodule_code": sub_data["code"],
        "submodule_name": sub_data["name"],
        "submodule_icon": sub_data.get("icon", "fa-solid fa-circle-dot"),
        "status": effective_status,
        "status_message": status_message,
        "is_active": is_active,
    }

    return True, is_active, context_info
