from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.permissions import IsBrand, IsBrandOrCreator
from apps.kyc import brand_kyc, services
from apps.kyc.models import KycProfile
from apps.kyc.serializers import (
    BankInput,
    BrandBankInput,
    BrandKycOutput,
    BrandPanInput,
    IdDocumentInput,
    KycOutput,
    PanInput,
)


def _out(kyc: KycProfile | None) -> Response:
    return Response(KycOutput(services.summary(kyc)).data)


class KycView(APIView):
    """GET the caller's KYC status and masked step details."""

    permission_classes = [IsBrandOrCreator]

    @extend_schema(responses=KycOutput)
    def get(self, request: Request) -> Response:
        return _out(KycProfile.objects.filter(user=request.user).first())


class KycPanView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=PanInput, responses=KycOutput)
    def put(self, request: Request) -> Response:
        data = PanInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        return _out(services.save_pan(user=request.user, pan=v["pan"], name_on_pan=v["nameOnPan"]))


class KycIdView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=IdDocumentInput, responses=KycOutput)
    def put(self, request: Request) -> Response:
        data = IdDocumentInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        return _out(services.save_id(user=request.user, id_type=v["idType"], upload_id=v["uploadId"]))


class KycBankView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=BankInput, responses=KycOutput)
    def put(self, request: Request) -> Response:
        data = BankInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        kyc = services.save_bank(
            user=request.user, holder_name=v["accountHolderName"], account_number=v["accountNumber"], ifsc=v["ifsc"]
        )
        return _out(kyc)


class KycSubmitView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=None, responses=KycOutput)
    def post(self, request: Request) -> Response:
        return _out(services.submit(user=request.user))


class BrandKycView(APIView):
    """GET /v1/brands/me/kyc/ — never 404s; a brand with nothing saved reads as `pending`."""

    permission_classes = [IsBrand]

    @extend_schema(responses=BrandKycOutput)
    def get(self, request: Request) -> Response:
        return Response(brand_kyc.status_payload(request.user))


class BrandKycPanView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=BrandPanInput, responses=BrandKycOutput)
    def post(self, request: Request) -> Response:
        data = BrandPanInput(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(brand_kyc.save_pan(user=request.user, pan=data.validated_data["pan"]))


class BrandKycBankView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=BrandBankInput, responses=BrandKycOutput)
    def post(self, request: Request) -> Response:
        data = BrandBankInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        payload = brand_kyc.save_bank(
            user=request.user, account_number=v["accountNumber"], ifsc=v["ifsc"], account_name=v["accountName"]
        )
        return Response(payload)
