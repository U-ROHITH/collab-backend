from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"


class Brand(BaseModel):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="brand")
    name = models.CharField(max_length=120)
    website = models.URLField(max_length=300, blank=True)
    industry = models.CharField(max_length=80, blank=True)
    logo_url = models.URLField(max_length=500, blank=True)
    legal_name = models.CharField(max_length=200, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    state_code = models.CharField(max_length=2, blank=True)  # for CGST/SGST vs IGST
    billing_address = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(gstin="") | Q(gstin__regex=GSTIN_REGEX), name="brand_gstin_format"),
        ]

    def __str__(self) -> str:
        return self.name


class BrandList(BaseModel):
    """A brand's saved/shortlist of creators."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="lists")
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=["brand", "name"], name="uniq_list_name_per_brand")]


class BrandListItem(BaseModel):
    list = models.ForeignKey(BrandList, on_delete=models.CASCADE, related_name="items")
    creator = models.ForeignKey("creators.Creator", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["list", "creator"], name="uniq_creator_per_list")]


class CartItem(BaseModel):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="cart_items")
    package = models.ForeignKey("creators.CreatorPackage", on_delete=models.CASCADE, related_name="+")
    brief = models.TextField(max_length=2000, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["brand", "package"], name="uniq_package_in_cart")]
