from uuid import UUID

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.admin_serializers import (
    AdminCategoryInput,
    AdminCategoryMergeInput,
    AdminCategoryOutput,
    AdminCategoryUpdateInput,
    AdminCreatorCurateInput,
    AdminCreatorCurateResult,
    AdminCreatorDetailOutput,
    AdminCreatorListOutput,
    AdminFeaturedCreatorsOutput,
    AdminFeaturedOrderInput,
)
from apps.creators.models import ApprovalStatus, Category, Creator
from apps.identity.models import AdminRole, UserStatus
from apps.identity.permissions import RequireAdminRoles
from apps.kyc.models import KycStatus
from apps.moderation.models import AbuseReport

VALID_BADGES = {"Verified Pro", "Top rated", "Rising"}


@extend_schema(tags=["Admin - Users"])
class AdminCreatorListView(APIView):
    """List creators with advanced admin filters (KYC, badge, featured, reports) and counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Search by name, slug, email, phone", required=False),
            OpenApiParameter("status", str, description="User status: active, suspended, deactivated", required=False),
            OpenApiParameter(
                "kyc", str, description="KYC status: verified, pending, rejected, not_started", required=False
            ),
            OpenApiParameter("badge", str, description="Filter by badge name", required=False),
            OpenApiParameter("featured", bool, description="Filter featured creators", required=False),
            OpenApiParameter("hasReports", bool, description="Filter creators with reports", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminCreatorListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = Creator.objects.select_related("user", "user__kyc")

        # Total counts
        total_creators = base_qs.count()
        suspended_creators = base_qs.filter(user__status=UserStatus.SUSPENDED).count()

        qs = base_qs

        # Search query
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(display_name__icontains=q)
                | Q(slug__icontains=q)
                | Q(user__email__icontains=q)
                | Q(user__phone__icontains=q)
            )

        # Status
        status_param = request.query_params.get("status")
        if status_param and status_param in UserStatus.values:
            qs = qs.filter(user__status=status_param)

        # KYC
        kyc_param = request.query_params.get("kyc")
        if kyc_param:
            if kyc_param == "not_started":
                qs = qs.filter(Q(user__kyc__isnull=True) | Q(user__kyc__status=KycStatus.NOT_STARTED))
            elif kyc_param in KycStatus.values:
                qs = qs.filter(user__kyc__status=kyc_param)

        # Badge
        badge = request.query_params.get("badge")
        if badge:
            qs = qs.filter(badges__contains=[badge])

        # Featured
        featured = request.query_params.get("featured")
        if featured is not None:
            is_featured = str(featured).lower() in ("true", "1")
            qs = qs.filter(featured=is_featured)

        # Has reports
        has_reports = request.query_params.get("hasReports")
        if has_reports is not None:
            reports_exist = str(has_reports).lower() in ("true", "1")
            report_subquery = AbuseReport.objects.filter(target_type="creator", target_id=OuterRef("id"))
            if reports_exist:
                qs = qs.annotate(has_rep=Exists(report_subquery)).filter(has_rep=True)
            else:
                qs = qs.annotate(has_rep=Exists(report_subquery)).filter(has_rep=False)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = Creator.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = AdminCreatorListOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "total": total_creators,
                    "suspended": suspended_creators,
                    "matching": total_matching,
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Users"])
class AdminCreatorDetailView(APIView):
    """Retrieve or curate creator profile, engagement metrics, order statistics, reports, and admin history."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminCreatorDetailOutput})
    def get(self, request: Request, id: UUID) -> Response:
        creator = Creator.objects.select_related("user", "user__kyc").filter(pk=id).first()
        if creator is None:
            raise NotFound("Creator not found.")
        return Response(AdminCreatorDetailOutput(creator).data)

    @extend_schema(request=AdminCreatorCurateInput, responses={200: AdminCreatorCurateResult})
    def patch(self, request: Request, id: UUID) -> Response:
        return AdminCreatorCurateView().patch(request, id)


@extend_schema(tags=["Admin - Users"])
class AdminCreatorCurateView(APIView):
    """Update creator badges (with KYC verification for Verified Pro), categories/tags, and featured status."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCreatorCurateInput, responses={200: AdminCreatorCurateResult})
    def patch(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminCreatorCurateInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        with transaction.atomic():
            creator = (
                Creator.objects.select_for_update(of=("self",))
                .select_related("user", "user__kyc")
                .filter(pk=id)
                .first()
            )
            if creator is None:
                raise NotFound("Creator not found.")

            update_fields = ["updated_at"]
            last_audit = None

            # Badges
            if "badges" in data:
                new_badges = data["badges"]
                for b in new_badges:
                    if b not in VALID_BADGES:
                        raise DomainError(f"Invalid badge '{b}'. Allowed: {VALID_BADGES}", code="INVALID_BADGE")
                    if b == "Verified Pro":
                        kyc = getattr(creator.user, "kyc", None) if creator.user else None
                        if not kyc or kyc.status != KycStatus.VERIFIED:
                            raise DomainError("Verified Pro badge requires verified KYC.", code="KYC_REQUIRED")

                old_badges = set(creator.badges or [])
                added_badges = set(new_badges) - old_badges
                removed_badges = old_badges - set(new_badges)

                for b in added_badges:
                    last_audit = record_audit(
                        admin=request.user,
                        action="creator.badge_add",
                        target=creator,
                        detail={"badge": b},
                    )
                for b in removed_badges:
                    last_audit = record_audit(
                        admin=request.user,
                        action="creator.badge_remove",
                        target=creator,
                        detail={"badge": b},
                    )

                creator.badges = sorted(new_badges)
                update_fields.append("badges")

            # Tags
            if "tags" in data:
                new_tags = data["tags"]
                if len(new_tags) > 4:
                    raise DomainError("A maximum of 4 tags is allowed.", code="TOO_MANY_TAGS")

                valid_categories = set(Category.objects.filter(is_active=True).values_list("name", flat=True))
                for tag in new_tags:
                    if tag not in valid_categories:
                        raise DomainError(f"Tag '{tag}' does not match an existing category.", code="INVALID_TAG")

                last_audit = record_audit(
                    admin=request.user,
                    action="creator.tags_edit",
                    target=creator,
                    detail={"tags": new_tags, "previousTags": creator.tags or []},
                )
                creator.tags = new_tags
                update_fields.append("tags")

            # Featured
            if "featured" in data:
                new_featured = data["featured"]
                if new_featured != creator.featured:
                    if new_featured:
                        last_audit = record_audit(
                            admin=request.user,
                            action="creator.feature",
                            target=creator,
                            detail={},
                        )
                        creator.featured = True
                        if not creator.featured_order:
                            max_order = Creator.objects.filter(featured=True).count() + 1
                            creator.featured_order = max_order
                    else:
                        last_audit = record_audit(
                            admin=request.user,
                            action="creator.unfeature",
                            target=creator,
                            detail={},
                        )
                        creator.featured = False
                        creator.featured_order = 0
                    update_fields.extend(["featured", "featured_order"])

            creator.save(update_fields=list(set(update_fields)))

            if last_audit is None:
                last_audit = record_audit(
                    admin=request.user,
                    action="creator.tags_edit",
                    target=creator,
                    detail={"updated": list(data.keys())},
                )

            return Response(
                {
                    "creator": AdminCreatorDetailOutput(creator).data,
                    "audit": last_audit,
                }
            )


@extend_schema(tags=["Admin - Users"])
class AdminCategoryListView(APIView):
    """List all categories with creator counts, or create a new category."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminCategoryOutput(many=True)})
    def get(self, request: Request) -> Response:
        categories = Category.objects.annotate(creator_count=Count("id")).order_by("name")
        return Response(AdminCategoryOutput(categories, many=True).data)

    @extend_schema(request=AdminCategoryInput, responses={201: AdminCategoryOutput})
    def post(self, request: Request) -> Response:
        input_serializer = AdminCategoryInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        name = input_serializer.validated_data["name"]

        if Category.objects.filter(name__iexact=name).exists():
            raise Conflict("A category with this name already exists.", code="CATEGORY_EXISTS")

        slug = slugify(name) or name.lower()
        base_slug = slug
        counter = 1
        while Category.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        with transaction.atomic():
            cat = Category.objects.create(
                name=name,
                slug=slug,
                description=input_serializer.validated_data.get("description", ""),
                icon=input_serializer.validated_data.get("icon", ""),
            )
            record_audit(
                admin=request.user,
                action="category.add",
                target=cat,
                detail={"name": cat.name, "slug": cat.slug},
            )

        return Response(AdminCategoryOutput(cat).data, status=201)


@extend_schema(tags=["Admin - Users"])
class AdminCategoryDetailView(APIView):
    """Update or rename an existing category."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCategoryUpdateInput, responses={200: AdminCategoryOutput})
    def patch(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminCategoryUpdateInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        with transaction.atomic():
            cat = Category.objects.select_for_update(of=("self",)).filter(pk=id).first()
            if cat is None:
                raise NotFound("Category not found.")

            old_name = cat.name
            if "name" in data and data["name"] != cat.name:
                new_name = data["name"]
                if Category.objects.filter(name__iexact=new_name).exclude(pk=cat.pk).exists():
                    raise Conflict("A category with this name already exists.", code="CATEGORY_EXISTS")
                cat.name = new_name
                cat.slug = slugify(new_name) or new_name.lower()

            if "description" in data:
                cat.description = data["description"]
            if "icon" in data:
                cat.icon = data["icon"]
            if "isActive" in data:
                cat.is_active = data["isActive"]

            cat.save(update_fields=["name", "slug", "description", "icon", "is_active", "updated_at"])

            record_audit(
                admin=request.user,
                action="category.rename",
                target=cat,
                detail={"oldName": old_name, "newName": cat.name},
            )

        return Response(AdminCategoryOutput(cat).data)


@extend_schema(tags=["Admin - Users"])
class AdminCategoryMergeView(APIView):
    """Merge this category into another target category, updating all creators and deleting this one."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCategoryMergeInput, responses={200: AdminCategoryOutput})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminCategoryMergeInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        target_id = input_serializer.validated_data["targetCategoryId"]

        if id == target_id:
            raise Conflict("Cannot merge a category into itself.", code="INVALID_MERGE")

        with transaction.atomic():
            source_cat = Category.objects.select_for_update(of=("self",)).filter(pk=id).first()
            target_cat = Category.objects.select_for_update(of=("self",)).filter(pk=target_id).first()
            if not source_cat or not target_cat:
                raise NotFound("Source or target category not found.")

            source_name = source_cat.name
            target_name = target_cat.name

            # Update creators using source_name
            affected_creators = Creator.objects.filter(
                Q(categories__contains=[source_name]) | Q(tags__contains=[source_name])
            )
            for c in affected_creators:
                new_cats = [target_name if x == source_name else x for x in (c.categories or [])]
                c.categories = list(dict.fromkeys(new_cats))
                new_tags = [target_name if x == source_name else x for x in (c.tags or [])]
                c.tags = list(dict.fromkeys(new_tags))[:4]
                c.save(update_fields=["categories", "tags", "updated_at"])

            record_audit(
                admin=request.user,
                action="category.merge",
                target=target_cat,
                detail={
                    "mergedCategory": source_name,
                    "targetCategory": target_name,
                    "affectedCreators": affected_creators.count(),
                },
            )

            source_cat.delete()

        return Response(AdminCategoryOutput(target_cat).data)


@extend_schema(tags=["Admin - Users"])
class AdminFeaturedCreatorsView(APIView):
    """List featured creators and eligible high-rated creators for featuring."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminFeaturedCreatorsOutput})
    def get(self, request: Request) -> Response:
        featured = Creator.objects.select_related("user", "user__kyc").filter(featured=True).order_by("featured_order")
        eligible = (
            Creator.objects.select_related("user", "user__kyc")
            .filter(
                featured=False,
                approval_status=ApprovalStatus.APPROVED,
                user__status=UserStatus.ACTIVE,
            )
            .order_by("-rating_sum", "-created_at")[:50]
        )

        return Response(
            {
                "featured": AdminCreatorListOutput(featured, many=True).data,
                "eligible": AdminCreatorListOutput(eligible, many=True).data,
            }
        )


@extend_schema(tags=["Admin - Users"])
class AdminFeaturedOrderView(APIView):
    """Reorder and set the list of featured creators."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminFeaturedOrderInput, responses={200: AdminCreatorListOutput(many=True)})
    def put(self, request: Request) -> Response:
        input_serializer = AdminFeaturedOrderInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        creator_ids = input_serializer.validated_data["creatorIds"]

        with transaction.atomic():
            # Unfeature creators not in new list
            Creator.objects.filter(featured=True).exclude(pk__in=creator_ids).update(
                featured=False, featured_order=0
            )

            # Set new order
            for index, cid in enumerate(creator_ids):
                Creator.objects.filter(pk=cid).update(featured=True, featured_order=index + 1)

            record_audit(
                admin=request.user,
                action="featured.reorder",
                target=request.user,
                detail={"creatorIds": [str(c) for c in creator_ids]},
            )

        updated_featured = (
            Creator.objects.select_related("user", "user__kyc")
            .filter(pk__in=creator_ids)
            .order_by("featured_order")
        )
        return Response(AdminCreatorListOutput(updated_featured, many=True).data)
