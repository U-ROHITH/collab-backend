from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.views import my_brand
from apps.core.exceptions import NotFound
from apps.creators.serializers import money
from apps.creators.views import _my_creator
from apps.identity.permissions import IsBrand, IsCreator
from apps.money import services
from apps.money.serializers import (
    CheckoutSessionOutput,
    CreatorWalletOutput,
    PaymentCheckoutInput,
    PaymentReceiptOutput,
    PaymentVerifyInput,
    WithdrawalInput,
    WithdrawalOutput,
)


class CheckoutView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=PaymentCheckoutInput, responses={201: CheckoutSessionOutput})
    def post(self, request: Request) -> Response:
        data = PaymentCheckoutInput(data=request.data)
        data.is_valid(raise_exception=True)
        validated = data.validated_data

        brand = my_brand(request)
        session, orders = services.initiate_checkout(
            brand=brand,
            actor=request.user,
            order_id=validated.get("orderId"),
            package_id=validated.get("packageId"),
            cart=validated.get("cart", False),
            currency=validated.get("currency", "INR"),
        )

        return Response(
            {
                "sessionId": str(session.id),
                "gateway": "razorpay",
                "orderId": session.gateway_order_id,
                "amount": money(session.amount_paise),
                "amountPaise": session.amount_paise,
                "currency": session.currency,
                "key": getattr(settings, "RAZORPAY_KEY_ID", "rzp_test_collabmarketplace"),
                "brand": {
                    "name": brand.name,
                    "email": getattr(brand.owner, "email", ""),
                },
                "orders": [str(o.id) for o in orders],
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "writes"

    @extend_schema(request=PaymentVerifyInput, responses={200: PaymentReceiptOutput})
    def post(self, request: Request) -> Response:
        data = PaymentVerifyInput(data=request.data)
        data.is_valid(raise_exception=True)
        validated = data.validated_data

        # Handle webhook format
        if validated.get("event") == "payment.captured" and "payload" in validated:
            entity = validated["payload"].get("payment", {}).get("entity", {})
            gateway_order_id = entity.get("order_id") or ""
            payment_id = entity.get("id") or ""
            signature = ""
        else:
            gateway_order_id = validated.get("razorpayOrderId") or validated.get("orderId") or ""
            payment_id = validated.get("razorpayPaymentId") or validated.get("paymentId") or ""
            signature = validated.get("razorpaySignature") or validated.get("signature") or ""

        if not gateway_order_id or not payment_id:
            raise NotFound("Missing orderId or paymentId for verification.")

        brand = getattr(request.user, "brand", None) if request.user and request.user.is_authenticated else None

        receipt = services.verify_payment(
            gateway_order_id=gateway_order_id,
            payment_id=payment_id,
            signature=signature,
            brand=brand,
            metadata=validated.get("payload"),
        )
        return Response(PaymentReceiptOutput(receipt).data)


class CreatorWalletView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "user"

    @extend_schema(responses=CreatorWalletOutput)
    def get(self, request: Request) -> Response:
        creator = _my_creator(request)
        wallet, pending_paise = services.get_or_sync_wallet(creator)
        transactions = wallet.transactions.all()[:50]
        return Response(
            CreatorWalletOutput(
                {
                    "wallet": wallet,
                    "pending_paise": pending_paise,
                    "transactions": transactions,
                }
            ).data
        )


class WithdrawalRequestView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(request=WithdrawalInput, responses={201: WithdrawalOutput})
    def post(self, request: Request) -> Response:
        data = WithdrawalInput(data=request.data)
        data.is_valid(raise_exception=True)
        validated = data.validated_data

        amount_paise = validated.get("amount") or validated.get("amountPaise")
        if amount_paise is None:
            amount_paise = 0

        creator = _my_creator(request)
        withdrawal = services.request_withdrawal(
            creator=creator,
            amount_paise=amount_paise,
            payout_method=validated["payoutMethod"],
            payout_details=validated["payoutDetails"],
        )
        return Response(WithdrawalOutput(withdrawal).data, status=status.HTTP_201_CREATED)
