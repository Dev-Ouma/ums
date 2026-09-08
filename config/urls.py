from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from cms import views as cms_views

from django.views.generic import RedirectView

urlpatterns = [
    path("manage/academics/examinations/", include("university.examination_urls")),
    path("examinations/<path:rest>", RedirectView.as_view(url="/manage/academics/examinations/%(rest)s", permanent=False)),
    path("examinations/", RedirectView.as_view(url="/manage/academics/examinations/", permanent=False)),
    path("django-admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("cms.urls")),
    path("", include("university.urls")),
    # CMS pages resolve last so a hand-written route always wins over a
    # page slug that happens to collide with it.
    path("<slug:slug>/", cms_views.page, name="cms_page"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
