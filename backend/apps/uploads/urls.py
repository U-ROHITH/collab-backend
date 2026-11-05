from django.urls import path

from apps.uploads import views

urlpatterns = [
    path("uploads/", views.UploadCreateView.as_view()),
    path("uploads/<uuid:upload_id>/complete/", views.UploadCompleteView.as_view()),
    path("uploads/<uuid:upload_id>/download-url/", views.UploadDownloadUrlView.as_view()),
    path("creators/me/media/<uuid:media_id>/", views.GalleryItemView.as_view()),
]
