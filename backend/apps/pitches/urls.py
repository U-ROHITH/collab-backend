from django.urls import path

from apps.pitches import views

urlpatterns = [
    path("brands/<uuid:brand_id>/pitches/", views.BrandPitchCreateView.as_view()),
    path("creators/me/pitches/", views.CreatorPitchListView.as_view()),
    path("pitches/<uuid:pitch_id>/", views.PitchEditView.as_view()),
    path("pitches/<uuid:pitch_id>/read/", views.PitchReadView.as_view()),
    path("pitches/<uuid:pitch_id>/decline/", views.PitchDeclineView.as_view()),
    path("brands/me/pitches/", views.BrandPitchListView.as_view()),
]
