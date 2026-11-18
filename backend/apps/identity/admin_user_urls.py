from django.urls import path

from apps.brands import admin_views as brand_admin_views
from apps.creators import admin_views as creator_admin_views
from apps.identity import admin_account_views

urlpatterns = [
    # User Account Actions
    path(
        "<uuid:user_id>/suspend/",
        admin_account_views.AdminSuspendAccountView.as_view(),
        name="admin_user_suspend",
    ),
    path(
        "accounts/<uuid:user_id>/suspend/",
        admin_account_views.AdminSuspendAccountView.as_view(),
        name="admin_user_accounts_suspend",
    ),
    path(
        "<uuid:user_id>/reactivate/",
        admin_account_views.AdminReactivateAccountView.as_view(),
        name="admin_user_reactivate",
    ),
    path(
        "accounts/<uuid:user_id>/reactivate/",
        admin_account_views.AdminReactivateAccountView.as_view(),
        name="admin_user_accounts_reactivate",
    ),
    path(
        "<uuid:user_id>/password-reset/",
        admin_account_views.AdminPasswordResetView.as_view(),
        name="admin_user_password_reset",
    ),
    path(
        "accounts/<uuid:user_id>/password-reset/",
        admin_account_views.AdminPasswordResetView.as_view(),
        name="admin_user_accounts_password_reset",
    ),
    path(
        "<uuid:user_id>/sessions/",
        admin_account_views.AdminSessionsListView.as_view(),
        name="admin_user_sessions",
    ),
    path(
        "accounts/<uuid:user_id>/sessions/",
        admin_account_views.AdminSessionsListView.as_view(),
        name="admin_user_accounts_sessions",
    ),
    path(
        "<uuid:user_id>/sessions/revoke/",
        admin_account_views.AdminSessionsRevokeView.as_view(),
        name="admin_user_sessions_revoke",
    ),
    path(
        "accounts/<uuid:user_id>/sessions/revoke/",
        admin_account_views.AdminSessionsRevokeView.as_view(),
        name="admin_user_accounts_sessions_revoke",
    ),
    # Creators
    path("creators/", creator_admin_views.AdminCreatorListView.as_view(), name="admin_creators_list"),
    path("creators/<uuid:id>/", creator_admin_views.AdminCreatorDetailView.as_view(), name="admin_creator_detail"),
    # Brands
    path("brands/", brand_admin_views.AdminBrandListView.as_view(), name="admin_brands_list"),
    path("brands/<uuid:id>/", brand_admin_views.AdminBrandDetailView.as_view(), name="admin_brand_detail"),
    # Categories
    path("categories/", creator_admin_views.AdminCategoryListView.as_view(), name="admin_categories_list"),
    path(
        "categories/<uuid:id>/",
        creator_admin_views.AdminCategoryDetailView.as_view(),
        name="admin_category_detail",
    ),
    path(
        "categories/<uuid:id>/merge/",
        creator_admin_views.AdminCategoryMergeView.as_view(),
        name="admin_category_merge",
    ),
    # Featured Creators
    path(
        "featured-creators/",
        creator_admin_views.AdminFeaturedCreatorsView.as_view(),
        name="admin_featured_creators",
    ),
    path(
        "featured-creators/order/",
        creator_admin_views.AdminFeaturedOrderView.as_view(),
        name="admin_featured_creators_order",
    ),
]
