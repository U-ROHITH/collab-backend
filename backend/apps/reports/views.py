from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports import services
from apps.reports.serializers import ReportsOverviewOutput


class ReportsOverviewView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "user"

    @extend_schema(responses=ReportsOverviewOutput)
    def get(self, request: Request) -> Response:
        data = services.get_user_overview_metrics(request.user)
        return Response(data)


class ExportOrdersCsvView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "user"

    @extend_schema(responses={200: str})
    def get(self, request: Request) -> HttpResponse:
        csv_data = services.generate_orders_csv(request.user)
        response = HttpResponse(csv_data, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="orders.csv"'
        return response


class ExportCampaignsCsvView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "user"

    @extend_schema(responses={200: str})
    def get(self, request: Request) -> HttpResponse:
        csv_data = services.generate_campaigns_csv(request.user)
        response = HttpResponse(csv_data, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="campaigns.csv"'
        return response


class ExportTransactionsCsvView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "user"

    @extend_schema(responses={200: str})
    def get(self, request: Request) -> HttpResponse:
        csv_data = services.generate_transactions_csv(request.user)
        response = HttpResponse(csv_data, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
        return response
