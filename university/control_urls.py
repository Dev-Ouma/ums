from django.urls import path
from . import control_views as v
app_name='control'
urlpatterns=[
    path('status/',v.status,name='status'),path('',v.dashboard,name='dashboard'),
    path('incidents/new/',v.restriction_edit,name='restriction_create'),
    path('incidents/<int:pk>/',v.restriction_detail,name='restriction_detail'),
    path('incidents/<int:pk>/edit/',v.restriction_edit,name='restriction_edit'),
    path('incidents/<int:pk>/action/<str:action>/',v.restriction_action,name='restriction_action'),
    path('incidents/<int:pk>/sessions/',v.sessions,name='sessions'),
    path('messages/',v.message_list,name='messages'),path('messages/new/',v.message_edit,name='message_create'),
    path('messages/<int:pk>/',v.message_detail,name='message_detail'),path('messages/<int:pk>/edit/',v.message_edit,name='message_edit'),
    path('messages/<int:pk>/action/<str:action>/',v.message_action,name='message_action'),
    path('messages/<int:pk>/receipt/<str:action>/',v.receipt,name='receipt'),
    path('templates/',v.templates,name='templates'),path('templates/new/',v.template_edit,name='template_create'),
    path('templates/<int:pk>/edit/',v.template_edit,name='template_edit'),path('health/',v.health,name='health'),
]
