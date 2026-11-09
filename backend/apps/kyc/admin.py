from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.core.exceptions import DomainError
from apps.identity.models import AuditLog
from apps.kyc import services
from apps.kyc.models import KycProfile
from apps.uploads import storage


@admin.register(KycProfile)
class KycProfileAdmin(admin.ModelAdmin):
    """Review queue. Ops compare the ID document with the name/PAN; full numbers stay encrypted.

    To reject: type the reason in "Rejection reason", save, then run "Reject" on the row.
    """

    list_display = ("user", "status", "pan_name", "pan_last4", "id_type", "bank_ifsc", "submitted_at")
    list_filter = ("status", "id_type")
    search_fields = ("user__email", "user__phone", "pan_name", "bank_holder_name")
    fields = (
        "user",
        "status",
        "pan_name",
        "pan_last4",
        "id_type",
        "id_document_link",
        "bank_holder_name",
        "bank_account_last4",
        "bank_ifsc",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "rejection_reason",
    )
    readonly_fields = tuple(f for f in fields if f != "rejection_reason")
    actions = ["verify", "reject"]

    def change_view(self, request: HttpRequest, object_id: str, form_url: str = "", extra_context=None):  # type: ignore[no-untyped-def]
        # The page shows a signed link to the ID document, so every open is an audited PII read.
        if request.method == "GET":
            AuditLog.objects.create(
                actor=request.user, action="kyc.document_viewed", entity_type="kyc", entity_id=str(object_id)
            )
        return super().change_view(request, object_id, form_url, extra_context)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: KycProfile | None = None) -> bool:
        return False

    @admin.display(description="ID document")
    def id_document_link(self, obj: KycProfile) -> str:
        doc = obj.id_document
        if doc is None or not doc.storage_key:
            return "-"
        try:
            url = storage.signed_download_url(doc.bucket, doc.storage_key, 300)
        except storage.StorageError:
            return "Storage unavailable"

        return format_html('<a href="{}" target="_blank" rel="noopener">Open (link valid 5 min)</a>', url)

    @admin.action(description="Verify selected (submitted) KYC")
    def verify(self, request: HttpRequest, queryset: QuerySet[KycProfile]) -> None:
        self._review(request, queryset, approve=True)

    @admin.action(description="Reject selected (uses each row's rejection reason)")
    def reject(self, request: HttpRequest, queryset: QuerySet[KycProfile]) -> None:
        self._review(request, queryset, approve=False)

    def _review(self, request: HttpRequest, queryset: QuerySet[KycProfile], *, approve: bool) -> None:
        done = 0
        for kyc in queryset:
            try:
                services.review(actor=request.user, kyc_id=kyc.pk, approve=approve, reason=kyc.rejection_reason)
                done += 1
            except DomainError as exc:
                self.message_user(request, f"{kyc.user}: {exc.message}", messages.ERROR)
        if done:
            self.message_user(request, f"{'Verified' if approve else 'Rejected'} {done} profile(s).", messages.SUCCESS)
