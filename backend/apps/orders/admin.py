from django.contrib import admin

from apps.orders.models import Dispute, Order, OrderEvent


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "event", "from_status", "to_status", "actor", "actor_role", "reason")

    def has_add_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only: order state changes only through services/state machine."""

    list_display = ("id", "title", "brand", "creator", "status", "brand_total_paise", "created_at")
    list_filter = ("status", "source_type")
    search_fields = ("id", "title", "brand__name", "creator__display_name")
    inlines = [OrderEventInline]

    def has_change_permission(self, request: object, obj: object = None) -> bool:
        return False

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("order", "opened_by", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("order", "opened_by", "reason", "status", "resolved_by", "resolved_at")
