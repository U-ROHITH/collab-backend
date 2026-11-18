import hashlib
import hmac
import json

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
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
from apps.money.models import PaymentSession
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
                "key": settings.RAZORPAY_KEY_ID,
                "brand": {
                    "name": brand.name,
                    "email": getattr(brand.owner, "email", ""),
                },
                "orders": [str(order.id) for order in orders],
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentVerifyView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=PaymentVerifyInput, responses={200: PaymentReceiptOutput})
    def post(self, request: Request) -> Response:
        data = PaymentVerifyInput(data=request.data)
        data.is_valid(raise_exception=True)
        validated = data.validated_data

        gateway_order_id = validated.get("razorpayOrderId") or validated.get("orderId") or ""
        payment_id = validated.get("razorpayPaymentId") or validated.get("paymentId") or ""
        signature = validated.get("razorpaySignature") or validated.get("signature") or ""

        if not gateway_order_id or not payment_id or not signature:
            raise ValidationError("Razorpay order ID, payment ID and signature are required.")

        brand = my_brand(request)

        # Use the order ID stored by our server, not an untrusted browser value.
        session = PaymentSession.objects.filter(
            gateway_order_id=gateway_order_id,
            brand=brand,
            gateway="razorpay",
        ).first()

        if session is None:
            raise NotFound("Payment session not found.")

        key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
        if not key_secret:
            raise services.PaymentGatewayUnavailable()

        expected_signature = hmac.new(
            key_secret.encode("utf-8"),
            f"{session.gateway_order_id}|{payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, signature.lower()):
            raise PermissionDenied("Invalid Razorpay payment signature.")

        # Confirm captured status and authoritative amount/currency with Razorpay.
        payment = services.fetch_razorpay_payment(payment_id)

        if (
            payment.get("id") != payment_id
            or payment.get("order_id") != session.gateway_order_id
            or payment.get("status") != "captured"
            or payment.get("captured") is not True
            or type(payment.get("amount")) is not int
            or not isinstance(payment.get("currency"), str)
        ):
            raise ValidationError("Razorpay has not confirmed a matching captured payment.")

        receipt = services.verify_payment(
            gateway_order_id=session.gateway_order_id,
            payment_id=payment_id,
            signature=signature,
            amount_paise=payment["amount"],
            currency=payment["currency"],
            brand=brand,
        )

        return Response(PaymentReceiptOutput(receipt).data)


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class PaymentWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "writes"

    def post(self, request: Request) -> Response:
        if not getattr(settings, "RAZORPAY_ENABLED", False):
            raise services.PaymentGatewayUnavailable()

        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        if not webhook_secret:
            raise services.PaymentGatewayUnavailable()

        # Verify the exact original bytes before parsing JSON.
        raw_body = request.body
        received_signature = request.headers.get("X-Razorpay-Signature", "")

        if len(received_signature) != 64:
            raise PermissionDenied("Invalid Razorpay webhook signature.")

        expected_signature = hmac.new(
            webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, received_signature.lower()):
            raise PermissionDenied("Invalid Razorpay webhook signature.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError("Invalid webhook JSON.") from exc

        if not isinstance(payload, dict):
            raise ValidationError("Invalid webhook payload.")

        if payload.get("event") != "payment.captured":
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        event_payload = payload.get("payload")
        payment_payload = (
            event_payload.get("payment")
            if isinstance(event_payload, dict)
            else None
        )
        entity = (
            payment_payload.get("entity")
            if isinstance(payment_payload, dict)
            else None
        )

        if not isinstance(entity, dict):
            raise ValidationError("Webhook does not contain a valid payment entity.")

        payment_id = entity.get("id")
        gateway_order_id = entity.get("order_id")
        event_amount = entity.get("amount")
        event_currency = entity.get("currency")

        if (
            not isinstance(payment_id, str)
            or not isinstance(gateway_order_id, str)
            or type(event_amount) is not int
            or not isinstance(event_currency, str)
            or entity.get("status") != "captured"
            or entity.get("captured") is not True
        ):
            raise ValidationError("Webhook does not contain a valid captured payment.")

        # Confirm the signed event against Razorpay's authoritative payment API.
        payment = services.fetch_razorpay_payment(payment_id)

        if (
            payment.get("id") != payment_id
            or payment.get("order_id") != gateway_order_id
            or type(payment.get("amount")) is not int
            or payment.get("amount") != event_amount
            or payment.get("currency") != event_currency
            or payment.get("status") != "captured"
            or payment.get("captured") is not True
        ):
            raise ValidationError(
                "Razorpay payment details do not match the captured webhook event."
            )

        services.verify_payment(
            gateway_order_id=gateway_order_id,
            payment_id=payment_id,
            signature=received_signature,
            amount_paise=payment["amount"],
            currency=payment["currency"],
        )

        return Response({"status": "ok"}, status=status.HTTP_200_OK)

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