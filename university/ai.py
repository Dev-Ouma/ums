"""
Resilient, comprehensive AI assistant and predictive engine for UMS.

Runs 100% offline with zero external API key requirements, querying live
database records across all university modules (Admissions, Academics, Exams,
Fees, Hostels, Library, Graduation, Attachment, etc.).

Optionally supports hybrid LLM augmentation (Gemini / OpenAI) if an API key
is provided in the environment, with instant silent fallback to the local
deterministic knowledge engine.
"""
from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from statistics import mean

from django.db.models import Avg, Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from . import services

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Defensive Utility Helpers
# --------------------------------------------------------------------------
def _get_user_name(user) -> str:
    """Safely extract the first name or username of any user object."""
    if not user or not getattr(user, "is_authenticated", False):
        return "there"
    display = getattr(user, "display_name", "") or ""
    if display and display.strip():
        return display.strip().split()[0]
    first = getattr(user, "first_name", "") or ""
    if first and first.strip():
        return first.strip()
    username = getattr(user, "username", "") or ""
    if username and username.strip():
        return username.strip()
    return "there"


def _safe_student_stats(student) -> dict:
    """Safely fetch student stats without raising exceptions if data is empty or missing."""
    default_stats = {
        "courses": 0,
        "attendance_pct": 0.0,
        "avg_marks": 0.0,
        "gpa": 0.0,
        "pending_assignments": 0,
        "submission_rate": 0.0,
        "fee_due": 0.0,
        "weakest_subject": None,
    }
    if not student:
        return default_stats
    try:
        stats = services.student_stats(student)
        # Ensure all expected keys exist and are valid numbers
        for k, v in default_stats.items():
            if k not in stats or stats[k] is None:
                stats[k] = v
        return stats
    except Exception as exc:
        logger.warning("Failed to calculate student stats for %s: %s", student, exc)
        return default_stats


# --------------------------------------------------------------------------
# Performance predictor
# --------------------------------------------------------------------------
def predict_performance(attendance_pct: float, avg_marks: float,
                        submission_rate: float) -> dict:
    """
    Predict a student's likely end-of-term score (0-100) from three signals.

    Weighted model (explainable):
        score = 0.35*attendance + 0.50*avg_marks + 0.15*submission_rate
    """
    try:
        attendance_pct = max(0.0, min(100.0, float(attendance_pct or 0.0)))
        avg_marks = max(0.0, min(100.0, float(avg_marks or 0.0)))
        submission_rate = max(0.0, min(100.0, float(submission_rate or 0.0)))
    except (ValueError, TypeError):
        attendance_pct = 0.0
        avg_marks = 0.0
        submission_rate = 0.0

    projected = 0.35 * attendance_pct + 0.50 * avg_marks + 0.15 * submission_rate
    projected = round(projected, 1)

    if projected >= 75:
        band, tone, icon = "On track for distinction", "success", "fa-trophy"
    elif projected >= 60:
        band, tone, icon = "Comfortable pass expected", "info", "fa-thumbs-up"
    elif projected >= 45:
        band, tone, icon = "Needs attention", "warning", "fa-triangle-exclamation"
    else:
        band, tone, icon = "High risk — intervene", "danger", "fa-circle-exclamation"

    drivers = []
    if attendance_pct < 75:
        drivers.append("Low attendance is pulling the projection down.")
    if avg_marks < 50:
        drivers.append("Recent exam averages are below the pass line.")
    if submission_rate < 60:
        drivers.append("Several assignments are still unsubmitted.")
    if not drivers:
        drivers.append("All signals are healthy — keep the momentum going.")

    return {
        "projected": projected,
        "band": band,
        "tone": tone,
        "icon": icon,
        "drivers": drivers,
        "inputs": {
            "attendance": round(attendance_pct, 1),
            "avg_marks": round(avg_marks, 1),
            "submission_rate": round(submission_rate, 1),
        },
    }


def student_prediction(student) -> dict:
    """Safely return prediction for a given StudentProfile."""
    stats = _safe_student_stats(student)
    return predict_performance(stats["attendance_pct"], stats["avg_marks"],
                               stats["submission_rate"])


def at_risk_students(limit: int = 8) -> list[dict]:
    """Return students whose signals fall below healthy thresholds."""
    from accounts.models import StudentProfile

    rows = []
    try:
        for student in StudentProfile.objects.select_related("user", "program"):
            stats = _safe_student_stats(student)
            pred = predict_performance(stats["attendance_pct"], stats["avg_marks"],
                                       stats["submission_rate"])
            if pred["projected"] < 60 or stats["attendance_pct"] < 75:
                rows.append({
                    "student": student,
                    "attendance": stats["attendance_pct"],
                    "avg_marks": stats["avg_marks"],
                    "projected": pred["projected"],
                    "tone": pred["tone"],
                })
        rows.sort(key=lambda r: r["projected"])
    except Exception as exc:
        logger.exception("Error identifying at-risk students: %s", exc)
    return rows[:limit]


def study_recommendations(student) -> list[dict]:
    """Provide tailored academic advice based on student indicators."""
    stats = _safe_student_stats(student)
    recs = []
    if stats["attendance_pct"] < 80:
        recs.append({"icon": "fa-calendar-check", "color": "#e17055",
                     "text": "Attend the next 5 classes without a gap to lift attendance above 80%."})
    if stats["pending_assignments"] > 0:
        recs.append({"icon": "fa-file-pen", "color": "#0984e3",
                     "text": f"You have {stats['pending_assignments']} pending assignment(s). "
                             "Submit the earliest due one today."})
    if stats["avg_marks"] < 60:
        recs.append({"icon": "fa-book-open-reader", "color": "#6C5CE7",
                     "text": "Book a revision slot for your weakest subject and attempt past papers."})
    weakest = stats.get("weakest_subject")
    if weakest:
        recs.append({"icon": "fa-bullseye", "color": "#00b894",
                     "text": f"Focus area: {weakest} — schedule 2 focused sessions this week."})
    if not recs:
        recs.append({"icon": "fa-star", "color": "#00b894",
                     "text": "You're performing well across the board. Aim for a distinction!"})
    return recs


# --------------------------------------------------------------------------
# Database Counts & Context
# --------------------------------------------------------------------------
def _kb_counts() -> dict:
    from accounts.models import FacultyProfile, StudentProfile
    from .models import Course, Department, Program, School
    try:
        return {
            "students": StudentProfile.objects.count(),
            "faculty": FacultyProfile.objects.count(),
            "courses": Course.objects.count(),
            "departments": Department.objects.count(),
            "schools": School.objects.count(),
            "programs": Program.objects.count(),
        }
    except Exception as exc:
        logger.warning("Error fetching knowledge base counts: %s", exc)
        return {
            "students": 0, "faculty": 0, "courses": 0,
            "departments": 0, "schools": 0, "programs": 0
        }


# --------------------------------------------------------------------------
# Optional Hybrid LLM Integration (Gemini / OpenAI)
# --------------------------------------------------------------------------
def _query_llm_if_configured(prompt: str, user) -> str | None:
    """
    Attempt an external LLM request if GEMINI_API_KEY or OPENAI_API_KEY is present.
    Has a strict 3-second timeout and fails gracefully to None if offline.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not gemini_key and not openai_key:
        return None

    import requests

    role_str = "student" if getattr(user, "is_student", False) else ("faculty" if getattr(user, "is_faculty", False) else "staff")
    system_instruction = (
        "You are the official UMS (University Management System) Campus AI Assistant. "
        f"The current user is named '{_get_user_name(user)}' and has the role '{role_str}'. "
        "Provide direct, helpful, polite answers related to university academics, admissions, "
        "finance/fees, exams, course registration, graduation, hostels, and library. "
        "Keep answers concise (under 3 paragraphs) and formatted in safe HTML with bold tags <b> and bullet points <ul><li>."
    )

    # 1. Try Gemini
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [
                    {"role": "user", "parts": [{"text": f"{system_instruction}\n\nUser Question: {prompt}"}]}
                ],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400}
            }
            res = requests.post(url, json=payload, timeout=3.0)
            if res.status_code == 200:
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
        except Exception as exc:
            logger.info("Gemini LLM request failed or timed out: %s", exc)

    # 2. Try OpenAI
    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 400
            }
            res = requests.post(url, headers=headers, json=payload, timeout=3.0)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.info("OpenAI LLM request failed or timed out: %s", exc)

    return None


# --------------------------------------------------------------------------
# Multi-Domain Intent Router
# --------------------------------------------------------------------------
def assistant_reply(text: str, user) -> dict:
    """
    Main intent router. Guarantees zero crashes and full coverage
    across all UMS services.
    """
    try:
        return _assistant_reply_inner(text, user)
    except Exception as exc:
        logger.exception("AI assistant encountered unhandled exception: %s", exc)
        return {
            "reply": (
                "I'm here to help you navigate UMS! What would you like to check? "
                "You can ask about <b>fees</b>, <b>course registration</b>, <b>exam cards</b>, "
                "<b>graduation clearance</b>, <b>hostel booking</b>, or <b>library catalog</b>."
            ),
            "icon": "fa-robot",
            "suggestions": _default_suggestions(user),
        }


def _assistant_reply_inner(text: str, user) -> dict:
    q = (text or "").lower().strip()
    user_name = _get_user_name(user)
    is_student = getattr(user, "is_student", False)
    is_faculty = getattr(user, "is_faculty", False)
    is_staff = getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)

    def resp(message: str, icon: str = "fa-robot", suggestions: list[str] | None = None) -> dict:
        return {
            "reply": message,
            "icon": icon,
            "suggestions": suggestions if suggestions is not None else _default_suggestions(user),
        }

    # 1. Blank / Empty query
    if not q:
        return resp(
            f"Hello {user_name}! I can help you with anything in UMS: "
            "course registration, fees and receipts, exam cards, graduation clearance, "
            "hostels, library, and campus directory. What can I do for you today?",
            "fa-hand-sparkles"
        )

    # 2. Greetings and Capabilities
    if re.search(r"\b(hi|hello|hey|namaste|morning|afternoon|evening|habari|jambo|sup|yo)\b", q) or q in {"who are you", "what can you do", "help", "menu"}:
        return resp(
            f"Hello <b>{user_name}</b>! I am your <b>UMS Campus Assistant</b>.<br><br>"
            "Here are some of the things you can ask me about:<br>"
            "• <b>Finances & Fees:</b> Check balance, make payments, download statement.<br>"
            "• <b>Academics:</b> Course registration, semester units, class timetable.<br>"
            "• <b>Examinations:</b> Exam card, exam timetable, provisional results, GPA.<br>"
            "• <b>Student Services:</b> Hostels, library books, attachments, requests.<br>"
            "• <b>Graduation:</b> Multi-department clearance, certificates, convocation.<br>"
            "• <b>Directory:</b> Schools, departments, courses, and faculty contact.",
            "fa-hand-sparkles",
            suggestions=["Check my fees", "Register for courses", "My exam card", "Graduation clearance", "Hostel booking"]
        )

    # 3. Fees & Financial Services
    if any(w in q for w in ["fee", "fees", "payment", "pay", "due", "balance", "invoice", "receipt", "mpesa", "statement", "finance", "tuition"]):
        if is_student and hasattr(user, "student_profile"):
            s = _safe_student_stats(user.student_profile)
            fee_due = s["fee_due"]
            if fee_due > 0:
                msg = (
                    f"You have an outstanding fee balance of <b>KES {fee_due:,.2f}</b>.<br><br>"
                    "You can make payments instantly via M-Pesa, Card, or Bank transfer, "
                    "or download your certified statement below:<br>"
                    "<div class='mt-2 d-flex flex-wrap gap-2'>"
                    "<a href='/me/fees/pay/' class='btn btn-sm btn-primary'><i class='fa-solid fa-credit-card me-1'></i> Pay Online</a> "
                    "<a href='/me/fees/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-file-invoice-dollar me-1'></i> Fee Invoices</a> "
                    "<a href='/me/fees/statement/pdf/' class='btn btn-sm btn-outline-secondary'><i class='fa-solid fa-download me-1'></i> Fee Statement</a>"
                    "</div>"
                )
            else:
                msg = (
                    "<b>All your fees are fully settled!</b> 🎉 You have no pending balance.<br><br>"
                    "You can view your receipts and payment history below:<br>"
                    "<div class='mt-2 d-flex flex-wrap gap-2'>"
                    "<a href='/me/fees/' class='btn btn-sm btn-primary'><i class='fa-solid fa-receipt me-1'></i> View Receipts</a> "
                    "<a href='/me/fees/statement/pdf/' class='btn btn-sm btn-outline-secondary'><i class='fa-solid fa-download me-1'></i> Fee Statement</a>"
                    "</div>"
                )
            return resp(msg, "fa-wallet", suggestions=["Pay fees", "Download fee statement", "My exam card", "Course registration"])

        # Staff / Admin view
        try:
            total = services.total_fees_collected()
            billed = services.total_fees_billed()
            pct = round((total / billed * 100), 1) if billed > 0 else 0.0
            msg = (
                f"<b>University Financial Summary:</b><br>"
                f"• Total Fees Billed: <b>KES {billed:,.2f}</b><br>"
                f"• Total Collected: <b>KES {total:,.2f}</b> ({pct}% collection rate)<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/manage/reports/' class='btn btn-sm btn-primary'><i class='fa-solid fa-chart-line me-1'></i> Finance Reports</a>"
                "</div>"
            )
        except Exception:
            msg = "You can manage tuition billing and fee structures in the <b>Finance & Fees</b> module."
        return resp(msg, "fa-wallet", suggestions=["Finance reports", "Campus attendance", "How many students?"])

    # 4. Course Registration & Semester Registration
    if any(w in q for w in ["register", "registration", "enroll", "enrollment", "unit", "units", "add course", "drop course", "semester reg"]):
        if is_student:
            msg = (
                "<b>Course & Semester Registration:</b><br>"
                "To register for your semester units:<br>"
                "1. Complete semester registration.<br>"
                "2. Select your core and elective courses within the credit limit.<br>"
                "3. Submit for departmental approval.<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/register/' class='btn btn-sm btn-primary'><i class='fa-solid fa-pen-to-square me-1'></i> Register Units</a> "
                "<a href='/manage/academics/semester-reg/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-calendar-check me-1'></i> Semester Reg</a> "
                "<a href='/me/courses/' class='btn btn-sm btn-outline-secondary'><i class='fa-solid fa-book-open me-1'></i> My Courses</a>"
                "</div>"
            )
            return resp(msg, "fa-pen-to-square", suggestions=["My courses", "My exam card", "Exam timetable"])
        else:
            msg = (
                "<b>Academic Unit Registration:</b><br>"
                "You can review student unit registrations, approval queues, and course enrollment rolls:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/manage/academics/registrations/' class='btn btn-sm btn-primary'><i class='fa-solid fa-list-check me-1'></i> View Registrations</a>"
                "</div>"
            )
            return resp(msg, "fa-pen-to-square")

    # 5. Examinations, Exam Card, Timetable & Hall Ticket
    if any(w in q for w in ["exam card", "hall ticket", "exam pass", "examination card"]):
        if is_student and hasattr(user, "student_profile"):
            s = _safe_student_stats(user.student_profile)
            if s["fee_due"] > 0:
                fee_alert = f"<br>⚠️ <i>Note: You have a pending fee balance of KES {s['fee_due']:,.2f}. Ensure fee clearance for exam eligibility.</i>"
            else:
                fee_alert = "<br>✅ <i>Your fee account is cleared for examinations.</i>"
            msg = (
                "<b>Student Examination Card:</b><br>"
                "Your exam card lists all registered course units and confirmed examination venues."
                f"{fee_alert}<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/exam-card/' class='btn btn-sm btn-primary'><i class='fa-solid fa-id-card me-1'></i> View Exam Card</a> "
                "<a href='/academics/exam-card/pdf/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-download me-1'></i> Download PDF</a>"
                "</div>"
            )
            return resp(msg, "fa-id-card", suggestions=["View Exam Card", "Exam timetable", "My results"])
        return resp(
            "Students can download their examination cards directly at <b>/academics/exam-card/</b> once unit registration and fee criteria are met.",
            "fa-id-card"
        )

    # 6. Supplementary / Resit Exams
    if any(w in q for w in ["supplementary", "resit", "retake", "special exam"]):
        if is_student:
            msg = (
                "<b>Supplementary & Special Examinations:</b><br>"
                "If you need to sit for a supplementary or special exam for an eligible unit, apply through the portal:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/supplementary/' class='btn btn-sm btn-primary'><i class='fa-solid fa-repeat me-1'></i> Supplementary Application</a>"
                "</div>"
            )
            return resp(msg, "fa-repeat", suggestions=["My results", "Exam card"])
        return resp("Supplementary exam applications can be reviewed in the Academics administration panel.", "fa-repeat")

    # 7. Results, Marks, GPA & Transcripts
    if any(w in q for w in ["result", "marks", "grade", "gpa", "cgpa", "score", "transcript", "progressive report", "provisional"]):
        if is_student and hasattr(user, "student_profile"):
            s = _safe_student_stats(user.student_profile)
            pred = student_prediction(user.student_profile)
            msg = (
                f"<b>Academic Performance Summary:</b><br>"
                f"• Average Score: <b>{s['avg_marks']}%</b><br>"
                f"• Cumulative GPA: <b>{s['gpa']}</b><br>"
                f"• Projected Term Score: <b>{pred['projected']}%</b> ({pred['band']})<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/me/results/' class='btn btn-sm btn-primary'><i class='fa-solid fa-square-poll-vertical me-1'></i> View Results</a> "
                "<a href='/academics/provisional-transcript/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-file-lines me-1'></i> Provisional Transcript</a> "
                "<a href='/academics/academic-transcript/' class='btn btn-sm btn-outline-secondary'><i class='fa-solid fa-scroll me-1'></i> Official Transcript</a>"
                "</div>"
            )
            return resp(msg, "fa-graduation-cap", suggestions=["My GPA", "Study recommendations", "My exam card"])
        return resp(
            "Examination results, nominal rolls, and transcripts are accessible via the <b>Examinations & Results</b> module.",
            "fa-graduation-cap"
        )

    # 8. Attendance
    if "attendance" in q or "absent" in q or "present" in q:
        if is_student and hasattr(user, "student_profile"):
            s = _safe_student_stats(user.student_profile)
            pct = s["attendance_pct"]
            status_text = "On track! Keep maintaining regular class attendance." if pct >= 80 else "⚠️ Caution: Your attendance is below 80%. Ensure you attend upcoming sessions."
            msg = (
                f"Your overall attendance is <b>{pct}%</b> across {s['courses']} enrolled course(s).<br>"
                f"{status_text}<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/me/attendance/' class='btn btn-sm btn-primary'><i class='fa-solid fa-calendar-check me-1'></i> Detailed Attendance</a>"
                "</div>"
            )
            return resp(msg, "fa-calendar-check", suggestions=["Detailed attendance", "My results", "Study recommendations"])
        if is_faculty:
            msg = (
                "<b>Faculty Attendance Management:</b><br>"
                "You can mark student attendance and review lecture history for your assigned courses:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/teach/courses/' class='btn btn-sm btn-primary'><i class='fa-solid fa-clipboard-user me-1'></i> Take Attendance</a>"
                "</div>"
            )
            return resp(msg, "fa-calendar-check")
        try:
            agg = services.overall_attendance()
            msg = f"Campus-wide attendance is averaging <b>{agg}%</b> this term."
        except Exception:
            msg = "Campus attendance analytics are available under institutional reports."
        return resp(msg, "fa-calendar-check")

    # 9. Graduation & Multi-Department Clearance
    if any(w in q for w in ["graduat", "graduation", "clearance", "convocation", "degree certificate", "gown"]):
        if is_student:
            msg = (
                "<b>Graduation & Multi-Department Clearance:</b><br>"
                "To graduate, all prospective students must complete institutional clearance across:<br>"
                "1. <b>Finance:</b> Zero outstanding fee balance.<br>"
                "2. <b>Library:</b> All borrowed books returned, no overdue fines.<br>"
                "3. <b>Hostel:</b> Key return and room clearance.<br>"
                "4. <b>Department:</b> Academic completion and project approval.<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/graduation/' class='btn btn-sm btn-primary'><i class='fa-solid fa-award me-1'></i> Clearance Status</a> "
                "<a href='/academics/graduation/apply/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-paper-plane me-1'></i> Apply for Clearance</a>"
                "</div>"
            )
            return resp(msg, "fa-award", suggestions=["Clearance status", "Check my fees", "Library status"])
        return resp(
            "Graduation ceremonies, senate approval lists, and departmental clearance queues can be managed at <b>/manage/graduation/</b>.",
            "fa-award"
        )

    # 10. Hostels & Campus Accommodation
    if any(w in q for w in ["hostel", "hostels", "accommodation", "room", "bed", "housing", "dorm", "residence"]):
        if is_student:
            msg = (
                "<b>Hostel Accommodation Portal:</b><br>"
                "You can apply for on-campus hostel rooms, check bed allocation status, and download clearance slips:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/campus/hostels/' class='btn btn-sm btn-primary'><i class='fa-solid fa-hotel me-1'></i> Hostel Portal</a> "
                "<a href='/campus/hostels/apply/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-bed me-1'></i> Book a Room</a>"
                "</div>"
            )
            return resp(msg, "fa-hotel", suggestions=["Hostel portal", "Book a room", "Check my fees"])
        return resp(
            "Hostel blocks, room allocations, check-in/check-out logs, and occupancy reports are available at <b>/manage/hostels/</b>.",
            "fa-hotel"
        )

    # 11. Library & Past Examination Papers
    if any(w in q for w in ["library", "book", "books", "borrow", "catalog", "past paper", "past papers", "shelf"]):
        msg = (
            "<b>University Library & Digital Repository:</b><br>"
            "Access thousands of physical books, digital references, and archived past exam papers for study revision:<br>"
            "<div class='mt-2 d-flex flex-wrap gap-2'>"
            "<a href='/campus/library/' class='btn btn-sm btn-primary'><i class='fa-solid fa-book-bookmark me-1'></i> Library Portal & Search</a>"
            "</div>"
        )
        return resp(msg, "fa-book-bookmark", suggestions=["Library portal", "Study recommendations", "My courses"])

    # 12. Admissions & Applications
    if any(w in q for w in ["admiss", "admission", "apply online", "application status", "intake", "matriculate", "admission letter"]):
        msg = (
            "<b>Admissions & Prospective Students:</b><br>"
            "• Prospective students can apply online for certificate, diploma, undergraduate, and postgraduate programs.<br>"
            "• Track your application status and download official admission letters.<br><br>"
            "<div class='mt-2 d-flex flex-wrap gap-2'>"
            "<a href='/admissions/apply/' class='btn btn-sm btn-primary'><i class='fa-solid fa-user-plus me-1'></i> Apply for Admission</a> "
            "<a href='/admissions/status/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-magnifying-glass me-1'></i> Check Status</a>"
            "</div>"
        )
        return resp(msg, "fa-door-open", suggestions=["Apply for admission", "Check status", "How many courses?"])

    # 13. Industrial Attachment / Practicum
    if any(w in q for w in ["attachment", "practicum", "internship", "logbook"]):
        if is_student:
            msg = (
                "<b>Industrial Attachment & Practicum Management:</b><br>"
                "• Apply for attachment placement and download your official institutional introduction letter.<br>"
                "• Submit your weekly logbook entries for supervisor review and assessment.<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/attachment/' class='btn btn-sm btn-primary'><i class='fa-solid fa-briefcase me-1'></i> Attachment Portal</a> "
                "<a href='/academics/attachment/apply/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-file-pen me-1'></i> Apply Placement</a>"
                "</div>"
            )
            return resp(msg, "fa-briefcase", suggestions=["Attachment portal", "Apply placement", "My courses"])
        return resp("Industrial attachment placements, logbooks, and assessments are located under <b>/academics/attachment/</b>.", "fa-briefcase")

    # 14. QA Evaluation (Course & Lecturer Feedback)
    if any(w in q for w in ["evaluation", "evaluate", "lecturer evaluation", "course evaluation", "feedback", "rating"]):
        if is_student:
            msg = (
                "<b>Quality Assurance Course & Lecturer Evaluation:</b><br>"
                "Share your feedback on lecture delivery, course content, and learning resources:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/me/evaluation/' class='btn btn-sm btn-primary'><i class='fa-solid fa-star-half-stroke me-1'></i> Open Evaluation</a>"
                "</div>"
            )
            return resp(msg, "fa-star-half-stroke", suggestions=["Open evaluation", "My courses"])
        return resp("Student evaluation results and survey windows are managed under <b>/manage/evaluation/</b>.", "fa-star-half-stroke")

    # 15. Student Requests & Deferment
    if any(w in q for w in ["defer", "deferment", "leave of absence", "resume studies", "student request", "recommendation letter"]):
        if is_student:
            msg = (
                "<b>Student Special Requests:</b><br>"
                "Submit academic leave requests, deferments, resumption of studies, or official letters:<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/academics/requests/' class='btn btn-sm btn-primary'><i class='fa-solid fa-envelope-open-text me-1'></i> Student Requests</a> "
                "<a href='/academics/requests/resume/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-user-check me-1'></i> Resume Studies</a>"
                "</div>"
            )
            return resp(msg, "fa-envelope-open-text", suggestions=["Student requests", "My courses"])
        return resp("Student requests and deferrals can be processed via <b>/manage/academics/requests/</b>.", "fa-envelope-open-text")

    # 16. Academic Calendar & Semester Dates
    if (
        any(w in q for w in ["calendar", "term", "academic year", "holiday", "session", "academic date"])
        or ("semester" in q and not any(r in q for r in ["reg", "enroll", "register"]))
        or ("when" in q and any(w in q for w in ["start", "begin", "end", "close", "date", "class"]))
    ):
        from .models import AcademicTerm, AcademicYear
        try:
            active_year = AcademicYear.objects.filter(is_current=True).first()
            active_term = (
                AcademicTerm.objects.filter(is_current=True).first()
                or AcademicTerm.objects.filter(status=AcademicYear.Status.PUBLISHED).first()
                or AcademicTerm.objects.first()
            )
            year_name = active_year.name if active_year else "Current Year"
            term_str = ""
            if active_term:
                term_str = (
                    f"• Active Term: <b>{active_term.name}</b><br>"
                    f"• Dates: <b>{active_term.start_date.strftime('%d %b %Y')}</b> to <b>{active_term.end_date.strftime('%d %b %Y')}</b><br>"
                )
            msg = (
                f"<b>Academic Calendar & Dates:</b><br>"
                f"• Academic Year: <b>{year_name}</b><br>"
                f"{term_str}<br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/events/' class='btn btn-sm btn-primary'><i class='fa-solid fa-calendar-days me-1'></i> University Events</a> "
                "<a href='/notices/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-bullhorn me-1'></i> Campus Notices</a>"
                "</div>"
            )
        except Exception:
            msg = "View the official academic schedule, exam dates, and holidays under <b>University Events</b>."
        return resp(msg, "fa-calendar-days", suggestions=["University events", "Campus notices", "Exam timetable"])

    # 17. Specific Course Code or Title Search
    from .models import Course
    # Match course-code patterns like "CS 101", "CS101", "BIT 210", etc.
    course_code_match = re.search(r"\b([a-zA-Z]{2,4}\s?[0-9]{3,4}[a-zA-Z]?)\b", text)
    if course_code_match:
        code_candidate = course_code_match.group(1).replace(" ", "").upper()
        found_course = Course.objects.filter(Q(code__iexact=code_candidate) | Q(code__icontains=code_candidate)).first()
        if found_course:
            dept_title = found_course.department.name if found_course.department else "General"
            msg = (
                f"<b>{found_course.code} — {found_course.title}</b><br>"
                f"• Department: <b>{dept_title}</b><br>"
                f"• Credit Hours: <b>{found_course.credits}</b><br>"
                f"• Description: {found_course.description or 'No syllabus description provided.'}<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/catalog/' class='btn btn-sm btn-primary'><i class='fa-solid fa-book me-1'></i> Course Catalog</a>"
                "</div>"
            )
            return resp(msg, "fa-book-open", suggestions=["Course catalog", "Register for courses", "My courses"])

    # 18. General Courses & Popular Courses
    if "course" in q or "subject" in q or "curriculum" in q or "syllabus" in q:
        try:
            top = services.popular_courses(4)
            if top:
                names = "<br>• ".join(f"<b>{c.code}</b> — {c.title} ({c.enrolled_count} students)" for c in top)
                msg = (
                    f"<b>Popular Courses This Term:</b><br>• {names}<br><br>"
                    "<div class='mt-2 d-flex flex-wrap gap-2'>"
                    "<a href='/catalog/' class='btn btn-sm btn-primary'><i class='fa-solid fa-book me-1'></i> Browse Full Catalog</a>"
                    "</div>"
                )
                return resp(msg, "fa-book", suggestions=["Browse full catalog", "Register for courses", "My exam card"])
        except Exception:
            pass
        return resp(
            "Explore all university courses and curriculum requirements in the public catalog:<br>"
            "<a href='/catalog/' class='btn btn-sm btn-primary mt-2'>Course Catalog</a>",
            "fa-book"
        )

    # 19. Schools, Departments & Academic Programs
    if any(w in q for w in ["department", "departments", "school", "schools", "program", "programs", "dean", "hod"]):
        from .models import Department, School
        try:
            depts = list(Department.objects.values_list("name", flat=True)[:6])
            dept_list = ", ".join(depts) if depts else "Engineering, Computing, Business, Sciences"
            kb = _kb_counts()
            msg = (
                f"UMS hosts <b>{kb['schools']} Schools</b>, <b>{kb['departments']} Departments</b>, and <b>{kb['programs']} Programs</b>.<br><br>"
                f"<b>Key Departments:</b> {dept_list}.<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/catalog/' class='btn btn-sm btn-primary'><i class='fa-solid fa-building-columns me-1'></i> Programs & Catalog</a>"
                "</div>"
            )
        except Exception:
            msg = "UMS provides degrees across multiple faculties and accredited departments."
        return resp(msg, "fa-building-columns", suggestions=["Programs and catalog", "How many students?", "Campus notices"])

    # 20. Predictions & Recommendations
    if any(w in q for w in ["recommend", "advice", "improve", "study tip", "tips", "prediction", "predict"]):
        if is_student and hasattr(user, "student_profile"):
            recs = study_recommendations(user.student_profile)
            pred = student_prediction(user.student_profile)
            rec_text = "<br>• ".join(r["text"] for r in recs[:3])
            msg = (
                f"<b>AI Academic Recommendations:</b><br>"
                f"• Projected Term Performance: <b>{pred['projected']}%</b> ({pred['band']})<br><br>"
                f"<b>Actionable Next Steps:</b><br>• {rec_text}<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/ai/insights/' class='btn btn-sm btn-primary'><i class='fa-solid fa-chart-pie me-1'></i> Full AI Insights</a>"
                "</div>"
            )
            return resp(msg, "fa-lightbulb", suggestions=["Full AI insights", "My attendance", "My results"])

    # 21. At-Risk Students (Faculty / Staff)
    if any(w in q for w in ["risk", "weak", "struggling", "intervention", "at risk"]):
        rows = at_risk_students(5)
        if rows:
            names = "<br>• ".join(
                f"<b>{r['student'].user.display_name or r['student'].user.username}</b> "
                f"(Att: {r['attendance']}%, Avg: {r['avg_marks']}%, Projected: {r['projected']}%)"
                for r in rows
            )
            msg = (
                f"<b>{len(rows)} Student(s) identified for academic intervention:</b><br>• {names}<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/ai/insights/' class='btn btn-sm btn-primary'><i class='fa-solid fa-chart-pie me-1'></i> View Insights Dashboard</a>"
                "</div>"
            )
            return resp(msg, "fa-triangle-exclamation", suggestions=["View insights dashboard", "Campus attendance"])
        return resp("All monitored students are currently meeting academic health thresholds! 🎉", "fa-circle-check")

    # 22. System Admin, Backups, Modules, Security
    if any(w in q for w in ["backup", "backups", "audit", "recycle bin", "module", "modules", "go live", "security testing"]):
        if is_staff:
            msg = (
                "<b>System Administration & Infrastructure Controls:</b><br>"
                "• <b>Module Registry:</b> Enable or disable system features and routing.<br>"
                "• <b>Disaster Recovery:</b> Automated database snapshots and backups.<br>"
                "• <b>Audit Trails:</b> Real-time tamper-evident security logging.<br><br>"
                "<div class='mt-2 d-flex flex-wrap gap-2'>"
                "<a href='/system-admin/modules/' class='btn btn-sm btn-primary'><i class='fa-solid fa-cubes me-1'></i> Modules</a> "
                "<a href='/system-admin/backups/' class='btn btn-sm btn-outline-primary'><i class='fa-solid fa-server me-1'></i> Backups</a> "
                "<a href='/manage/audit-trails/' class='btn btn-sm btn-outline-secondary'><i class='fa-solid fa-shield-halved me-1'></i> Audit Logs</a>"
                "</div>"
            )
            return resp(msg, "fa-screwdriver-wrench")

    # 23. Account, Password & Profile
    if any(w in q for w in ["password", "reset password", "change password", "profile", "account", "login", "unlock"]):
        msg = (
            "<b>Account & Security Settings:</b><br>"
            "• You can change your password securely or update your contact details.<br>"
            "• Two-factor and session security can be reviewed in your settings.<br><br>"
            "<div class='mt-2 d-flex flex-wrap gap-2'>"
            "<a href='/accounts/password_change/' class='btn btn-sm btn-primary'><i class='fa-solid fa-key me-1'></i> Change Password</a>"
            "</div>"
        )
        return resp(msg, "fa-shield-halved", suggestions=["Change password", "My profile"])

    # 24. Campus Statistics & Overview
    if any(w in q for w in ["how many", "count", "number of", "total", "stats", "statistics", "campus summary"]):
        kb = _kb_counts()
        if "student" in q:
            return resp(f"There are currently <b>{kb['students']:,}</b> enrolled students.", "fa-users")
        if "faculty" in q or "teacher" in q or "professor" in q or "lecturer" in q:
            return resp(f"We have <b>{kb['faculty']:,}</b> active faculty members.", "fa-chalkboard-user")
        if "course" in q:
            return resp(f"The academic catalogue offers <b>{kb['courses']:,}</b> courses.", "fa-book")
        if "department" in q:
            return resp(f"There are <b>{kb['departments']}</b> academic departments across {kb['schools']} schools.", "fa-building-columns")

        return resp(
            f"<b>Campus At A Glance:</b><br>"
            f"• <b>{kb['students']:,}</b> Students<br>"
            f"• <b>{kb['faculty']:,}</b> Faculty Members<br>"
            f"• <b>{kb['courses']:,}</b> Courses<br>"
            f"• <b>{kb['departments']}</b> Departments in <b>{kb['schools']}</b> Schools",
            "fa-chart-pie",
            suggestions=["Check my fees", "Register for courses", "My exam card", "Course catalog"]
        )

    # 25. Optional Hybrid LLM attempt for open-ended queries
    llm_reply = _query_llm_if_configured(text, user)
    if llm_reply:
        return resp(llm_reply, "fa-brain", suggestions=_default_suggestions(user))

    # 26. Intelligent Resilient Fallback
    return resp(
        f"I can help you with anything across UMS! Here are quick topics you can explore:<br>"
        "• <b>Finances:</b> <a href='/me/fees/'>Pay fees & invoices</a><br>"
        "• <b>Academics:</b> <a href='/academics/register/'>Unit registration</a> and <a href='/catalog/'>Course catalog</a><br>"
        "• <b>Examinations:</b> <a href='/academics/exam-card/'>Exam card</a> and <a href='/me/results/'>Exam results</a><br>"
        "• <b>Services:</b> <a href='/campus/hostels/'>Hostel rooms</a> and <a href='/campus/library/'>Library search</a><br>"
        "• <b>Graduation:</b> <a href='/academics/graduation/'>Clearance portal</a>",
        "fa-circle-question",
        suggestions=["Check my fees", "Register for courses", "My exam card", "Graduation clearance", "Hostel booking"]
    )


def _default_suggestions(user) -> list[str]:
    """Provide relevant starter chips based on user role."""
    if getattr(user, "is_student", False):
        return ["Check my fees", "Register for courses", "My exam card", "Graduation clearance", "Hostel booking"]
    if getattr(user, "is_faculty", False):
        return ["At-risk students", "Campus attendance", "How many courses?", "Faculty courses"]
    return ["How many students?", "Total fees collected", "Campus attendance", "Popular courses", "System modules"]
