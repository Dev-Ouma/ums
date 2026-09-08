from django.urls import path

from . import views

app_name = "cms"

urlpatterns = [
    # --- manager UI (admin only) ---
    path("manage/site/", views.dashboard, name="dashboard"),
    path("manage/site/settings/", views.site_settings, name="settings"),

    path("manage/site/pages/", views.page_list, name="page_list"),
    path("manage/site/pages/new/", views.page_create, name="page_create"),
    path("manage/site/pages/<int:pk>/", views.page_edit, name="page_edit"),
    path("manage/site/pages/<int:pk>/delete/", views.page_delete, name="page_delete"),
    path("manage/site/pages/<int:pk>/status/", views.page_toggle_status, name="page_status"),

    path("manage/site/pages/<int:page_pk>/blocks/new/", views.block_create, name="block_create"),
    path("manage/site/blocks/<int:pk>/", views.block_edit, name="block_edit"),
    path("manage/site/blocks/<int:pk>/delete/", views.block_delete, name="block_delete"),
    path("manage/site/blocks/<int:pk>/move/<str:direction>/", views.block_move, name="block_move"),

    path("manage/site/menus/", views.menu_list, name="menu_list"),
    path("manage/site/menus/new/", views.menu_create, name="menu_create"),
    path("manage/site/menus/<int:pk>/", views.menu_edit, name="menu_edit"),
    path("manage/site/menus/<int:pk>/delete/", views.menu_delete, name="menu_delete"),

    path("manage/site/media/", views.media_library, name="media"),
    path("manage/site/media/<int:pk>/delete/", views.media_delete, name="media_delete"),
]
