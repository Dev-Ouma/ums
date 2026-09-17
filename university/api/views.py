"""
Read-only self-service API: what the authenticated principal (session or
API key -- see authentication.py) can see about themselves. Deliberately
narrow scope for a first API surface -- everything here wraps an existing
service function rather than duplicating its logic, and every endpoint is
"my own data only" so it needs no new permission model beyond "is this a
valid, active principal."
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from university.api.serializers import (
    ExamResultSerializer, FeeBalanceSerializer, MeSerializer,
)
from university.models import Exam


class MeView(APIView):
    """GET /api/v1/me/ -- basic identity of the authenticated principal."""

    def get(self, request):
        user = request.user
        data = {
            "id": user.pk, "username": user.username, "email": user.email,
            "first_name": user.first_name, "last_name": user.last_name,
            "role": getattr(user, "role", ""),
            "is_student": hasattr(user, "student_profile"),
            "is_faculty": hasattr(user, "faculty_profile"),
        }
        return Response(MeSerializer(data).data)


class MyFeeBalanceView(APIView):
    """GET /api/v1/me/fee-balance/ -- the authenticated student's own fee balance."""

    def get(self, request):
        student = getattr(request.user, "student_profile", None)
        if not student:
            return Response({"detail": "This account has no student profile."}, status=404)

        from university.payment_services import get_student_balance_summary
        summary = get_student_balance_summary(student)
        return Response(FeeBalanceSerializer(summary).data)


class MyExamResultsView(APIView):
    """
    GET /api/v1/me/exam-results/ -- the authenticated student's own
    results for PUBLISHED exams only. Unpublished marks are never exposed
    here, same rule the student-facing web statement already enforces.
    """

    def get(self, request):
        student = getattr(request.user, "student_profile", None)
        if not student:
            return Response({"detail": "This account has no student profile."}, status=404)

        results = student.results.filter(exam__status=Exam.Status.PUBLISHED).select_related(
            "exam__course", "exam__term")

        data = []
        for result in results:
            exam = result.exam
            grade = None
            if result.marks_obtained is not None and exam.max_marks:
                percentage = float(result.marks_obtained) / float(exam.max_marks) * 100
                grade = exam.grade_for(percentage)
            data.append({
                "exam_id": exam.pk, "exam_name": exam.name,
                "course_code": exam.course.code, "course_title": exam.course.title,
                "term": exam.term.name if exam.term_id else None,
                "cat_marks": result.cat_marks, "exam_marks": result.exam_marks,
                "marks_obtained": result.marks_obtained, "grade": grade,
                "published_at": exam.published_at,
            })
        return Response(ExamResultSerializer(data, many=True).data)
