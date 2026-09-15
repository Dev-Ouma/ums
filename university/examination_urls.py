from django.urls import path
from . import examination_views as views

from . import transcript_views

app_name = 'examinations'
urlpatterns = [
    path('transcripts/', transcript_views.portal, name='transcripts'),
    path('transcripts/student/<int:student_id>/', transcript_views.portal, name='student_transcripts'),
    path('transcripts/student/<int:student_id>/<str:kind>/', transcript_views.document, name='transcript_document'),
    path('', views.index, name='index'),
    path('new/', views.edit, name='create'),
    path('rooms/', views.setup, {'kind': 'rooms'}, name='rooms'),
    path('rooms/<int:pk>/', views.setup, {'kind': 'rooms'}, name='room_edit'),
    path('terms/', views.setup, {'kind': 'terms'}, name='terms'),
    path('terms/<int:pk>/', views.setup, {'kind': 'terms'}, name='term_edit'),
    path('grading/', views.setup, {'kind': 'grading'}, name='grading'),
    path('grading/<int:pk>/', views.setup, {'kind': 'grading'}, name='grading_edit'),
    path('report/', views.report, name='report'),
    path('workflow/<str:stage>/', views.workflow_queue, name='workflow_queue'),
    path('attendance/', views.attendance, name='attendance'),
    path('attendance/<int:pk>/', views.attendance, name='attendance_detail'),
    path('admission/', views.admission, name='admission'),
    path('results/', views.statement, name='statement'),
    path('results/student/<int:student_id>/', views.statement, name='student_statement'),
    path('results/<int:pk>/appeal/', views.appeal, name='appeal'),
    path('appeals/<int:pk>/resolve/', views.resolve, name='resolve'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/action/', views.action, name='action'),
    path('marks/', views.marks, name='marks_capture'),
    path('<int:pk>/marks/', views.marks, name='marks'),
    path('<int:pk>/register/', views.register, name='register'),
    # Examination Schedule Management
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/create/', views.schedule_create, name='schedule_create'),
    path('schedules/<int:pk>/edit/', views.schedule_edit, name='schedule_edit'),
    path('schedules/<int:pk>/detail/', views.schedule_detail, name='schedule_detail'),
    path('schedules/items/<int:item_id>/students/', views.schedule_item_students, name='schedule_item_students'),
    path('schedules/<int:pk>/delete/', views.schedule_delete, name='schedule_delete'),
    path('schedules/<int:pk>/publish/', views.schedule_publish, name='schedule_publish'),
    path('schedules/<int:pk>/toggle-status/', views.schedule_toggle_status, name='schedule_toggle_status'),
    path('schedules/api/courses/', views.schedule_courses_api, name='schedule_courses_api'),
]
