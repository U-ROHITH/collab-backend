from django.contrib import admin

from apps.brands.models import Brand


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "industry", "gstin", "created_at")
    search_fields = ("name", "legal_name", "gstin")
    readonly_fields = ("id", "owner", "created_at", "updated_at")
