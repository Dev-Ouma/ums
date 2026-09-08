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
    path('admission/', views.admission, name='admission'),
    path('results/', views.statement, name='statement'),
    path('results/student/<int:student_id>/', views.statement, name='student_statement'),
    path('results/<int:pk>/appeal/', views.appeal, name='appeal'),
    path('appeals/<int:pk>/resolve/', views.resolve, name='resolve'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/action/', views.action, name='action'),
    path('<int:pk>/marks/', views.marks, name='marks'),
    path('<int:pk>/register/', views.register, name='register'),
]
