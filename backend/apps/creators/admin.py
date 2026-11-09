from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.creators import services
from apps.creators.models import ApprovalStatus, Creator, CreatorPackage


class PackageInline(admin.TabularInline):
    model = CreatorPackage
    extra = 0
    fields = ("title", "platform", "price_paise", "delivery_days", "active")


@admin.register(Creator)
class CreatorAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slug", "approval_status", "verified", "location", "created_at")
    list_filter = ("approval_status", "verified", "primary_platform")
    search_fields = ("display_name", "slug", "location")
    readonly_fields = ("id", "user", "approval_status", "rating_sum", "rating_count", "created_at", "updated_at")
    inlines = [PackageInline]
    actions = ["approve", "reject", "mark_verified", "mark_unverified"]

    @admin.action(description="Approve selected creators")
    def approve(self, request: HttpRequest, queryset: QuerySet[Creator]) -> None:
        for creator in queryset:
            services.set_approval(actor=request.user, creator_id=creator.pk, status=ApprovalStatus.APPROVED)
        self.message_user(request, f"Approved {queryset.count()} creator(s).", messages.SUCCESS)

    @admin.action(description="Reject selected creators")
    def reject(self, request: HttpRequest, queryset: QuerySet[Creator]) -> None:
        for creator in queryset:
            services.set_approval(actor=request.user, creator_id=creator.pk, status=ApprovalStatus.REJECTED)
        self.message_user(request, f"Rejected {queryset.count()} creator(s).", messages.WARNING)

    @admin.action(description="Mark selected creators as verified")
    def mark_verified(self, request: HttpRequest, queryset: QuerySet[Creator]) -> None:
        queryset.update(verified=True)
        self.message_user(request, f"Marked {queryset.count()} creator(s) as verified.", messages.SUCCESS)

    @admin.action(description="Mark selected creators as unverified")
    def mark_unverified(self, request: HttpRequest, queryset: QuerySet[Creator]) -> None:
        queryset.update(verified=False)
        self.message_user(request, f"Marked {queryset.count()} creator(s) as unverified.", messages.WARNING)
