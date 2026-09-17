from django.urls import path

from university.api import views

app_name = "api_v1"

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("me/fee-balance/", views.MyFeeBalanceView.as_view(), name="my-fee-balance"),
    path("me/exam-results/", views.MyExamResultsView.as_view(), name="my-exam-results"),
]
