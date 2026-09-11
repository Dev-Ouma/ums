from datetime import timedelta
from decimal import Decimal
import subprocess
import tempfile
from pathlib import Path
import shutil

from django.urls import reverse
from university.tests_examinations import ExaminationTestBase
from university.models import AcademicTerm, Course, Exam, Result
from university.transcript_io import build_transcript_context, export_transcript_pdf
from university import examination_services as workflow


class TranscriptTests(ExaminationTestBase):
    def published(self, course=None, term=None, mark=80, **kwargs):
        exam = Exam.objects.create(course=course or self.course, term=term or self.term,
            name='Completed assessment', kind=Exam.Kind.FINAL, weight=100,
            status=Exam.Status.PUBLISHED, **kwargs)
        Result.objects.create(exam=exam, student=self.students[0], attendance='PRESENT',marks_obtained=mark)
        return exam

    def test_credit_weighting_and_saved_grade_scale(self):
        self.course.credits=6
        self.course.save()
        self.published(grade_bands=[{'grade':'DIST','minimum':0,'gp':4.5}])
        course=Course.objects.create(code='SMALL',title='Small course',department=self.program.department, credits=2)
        self.published(course=course,mark=50)
        ctx=build_transcript_context(self.students[0])
        self.assertEqual(ctx['cgpa'],Decimal('3.88'))
        self.assertEqual(workflow.student_statement(self.students[0]).gpa,ctx['cgpa'])
        from university.services import student_stats
        self.assertEqual(student_stats(self.students[0])['gpa'],ctx['cgpa'])
        self.assertEqual(Result.objects.get(exam__course=self.course,student=self.students[0]).grade_point,4.5)
        self.assertEqual(ctx['overall_credits_completed'],8)
        self.assertIn('DIST',ctx['grade_distribution'])

    def test_final_seventy_alone_is_incomplete(self):
        exam=self.published()
        exam.weight=70
        exam.save()
        self.assertEqual(build_transcript_context(self.students[0])['semesters'],[])

    def test_pending_marks_are_excluded(self):
        exam=self.published()
        exam.results.update(marks_obtained=None,attendance='PENDING')
        self.assertIsNone(build_transcript_context(self.students[0])['cgpa'])

    def test_unpublished_and_reopened_results_are_excluded(self):
        exam=self.published()
        for status in [Exam.Status.DRAFT,Exam.Status.APPROVED,Exam.Status.MARKING]:
            exam.status=status
            exam.save()
            self.assertFalse(build_transcript_context(self.students[0])['semesters'])

    def test_repeats_keep_attempts_but_earn_credits_once(self):
        self.published(mark=60)
        term=AcademicTerm.objects.create(name='Later',start_date=self.today+timedelta(days=100),end_date=self.today+timedelta(days=200))
        self.published(term=term,mark=80)
        ctx=build_transcript_context(self.students[0])
        self.assertEqual(ctx['overall_credits_attempted'],8)
        self.assertEqual(ctx['overall_credits_completed'],4)
        self.assertEqual(ctx['total_courses_completed'],1)
        self.assertEqual(ctx['cgpa'],Decimal('3.50'))
        self.assertEqual(ctx['semesters'][1]['credits_completed'],0)
        filtered=build_transcript_context(self.students[0],term)
        self.assertEqual(len(filtered['semesters']),1)
        self.assertEqual(filtered['semesters'][0]['cumulative_gpa'],Decimal('3.50'))
        self.assertEqual(filtered['period_gpa'],Decimal('4.00'))

    def test_latest_published_supplement_replaces_original(self):
        exam=self.published(mark=20)
        for i,mark in enumerate([60,80]):
            supplement=Exam.objects.create(course=self.course,term=self.term,original_exam=exam,
                kind=Exam.Kind.SUPPLEMENTARY,status=Exam.Status.PUBLISHED,date=self.today+timedelta(days=i))
            Result.objects.create(exam=supplement,student=self.students[0],attendance='PRESENT',marks_obtained=mark)
        ctx=build_transcript_context(self.students[0])
        self.assertEqual(ctx['cgpa'],Decimal('4.00'))
        self.assertEqual(ctx['overall_credits_attempted'],4)

    def test_student_admin_and_faculty_permissions_for_every_format(self):
        self.published()
        for user,allowed in [(self.students[0].user,True),(self.students[1].user,False),(self.admin,True),(self.lecturer,False)]:
            self.client.force_login(user)
            for kind in ['provisional','academic','performance']:
                url=reverse('examinations:transcript_document',args=[self.students[0].pk,kind])
                for suffix in ['', '?format=pdf','?format=pdf&download=1']:
                    response=self.client.get(url+suffix)
                    self.assertEqual(response.status_code,200 if allowed else 403)
                    if allowed:
                        self.assertIn('no-store',response['Cache-Control'])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code,302)

    def test_portal_selection_empty_states_and_invalid_requests(self):
        self.client.force_login(self.students[0].user)
        url=reverse('examinations:transcripts')
        self.assertContains(self.client.get(url),'No complete published results yet')
        self.published()
        self.assertContains(self.client.get(url),'Academic Performance')
        self.assertEqual(self.client.get(url+'?term=nope').status_code,404)
        self.assertEqual(self.client.get(url+'?term=999999').status_code,404)
        self.assertEqual(self.client.get(reverse('examinations:transcript_document',args=[self.students[0].pk,'bad'])).status_code,404)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url),'Select a student')

    def test_pdf_preview_download_are_identical_and_same_origin(self):
        self.published()
        self.client.force_login(self.students[0].user)
        url=reverse('examinations:transcript_document',args=[self.students[0].pk,'academic'])+'?format=pdf'
        inline=self.client.get(url)
        download=self.client.get(url+'&download=1')
        self.assertEqual(inline.content,download.content)
        self.assertEqual(inline['X-Frame-Options'],'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", inline['Content-Security-Policy'])
        self.assertTrue(inline['Content-Disposition'].startswith('inline'))
        self.assertTrue(download['Content-Disposition'].startswith('attachment'))
        self.assertTrue(inline.content.startswith(b'%PDF-'))

    def test_multi_page_pdf_escapes_names_and_repeats_table_headers(self):
        self.students[0].user.first_name='Ada <Registry> & Co'
        self.students[0].user.save()
        for i in range(65):
            course=Course.objects.create(code=f'COURSE{i:03}',title='Academic study & analytical methods '*3,department=self.program.department)
            self.published(course=course)
        ctx=build_transcript_context(self.students[0])
        data=export_transcript_pdf(self.students[0],ctx,kind='academic')
        self.assertTrue(data.startswith(b'%PDF-'))
        if shutil.which('pdftotext'):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'transcript.pdf'
                path.write_bytes(data)
                text=subprocess.check_output(['pdftotext','-layout',str(path),'-']).decode()
                self.assertIn('Ada <Registry> & Co',text)
                self.assertIn('COURSE064',text)
                self.assertIn('Page 2 of',text)
                self.assertGreater(text.count('Course title'),1)

    def test_zero_credit_results_do_not_invent_a_gpa(self):
        self.course.credits=0
        self.course.save()
        self.published()
        ctx=build_transcript_context(self.students[0])
        self.assertIsNone(ctx['cgpa'])
        self.assertEqual(ctx['overall_credits_completed'],0)
        self.client.force_login(self.students[0].user)
        self.assertContains(self.client.get(reverse('examinations:transcripts')),'N/A')

    def test_failure_counts_in_gpa_but_does_not_earn_credits(self):
        self.published(mark=20)
        ctx=build_transcript_context(self.students[0])
        self.assertEqual(ctx['cgpa'],Decimal('0.00'))
        self.assertEqual(ctx['overall_credits_completed'],0)
        self.assertEqual(ctx['overall_credits_attempted'],4)
        self.assertEqual(ctx['total_courses_completed'],0)

    def test_grading_does_not_round_percentage_across_a_boundary(self):
        self.published(mark=Decimal('69.99'))
        ctx=build_transcript_context(self.students[0])
        self.assertEqual(ctx['grade_distribution'],{'B':1})
        self.assertEqual(Result.objects.get(student=self.students[0]).grade,'B')
