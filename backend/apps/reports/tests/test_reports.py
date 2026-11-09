import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_reports_overview_unauthenticated(anon: APIClient) -> None:
    resp = anon.get("/v1/reports/overview/")
    assert resp.status_code == 401


def test_reports_overview_brand(brand: APIClient, campaign_payload: dict) -> None:
    # Create campaign
    brand.post("/v1/campaigns/", campaign_payload, format="json")

    resp = brand.get("/v1/reports/overview/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["role"] == "brand"
    assert data["metrics"]["totalCampaigns"] >= 1
    assert "totalSpendPaise" in data["metrics"]
    assert "totalOrders" in data["metrics"]


def test_reports_overview_creator(creator: APIClient) -> None:
    resp = creator.get("/v1/reports/overview/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["role"] == "creator"
    assert "totalEarningsPaise" in data["metrics"]
    assert "activeOrders" in data["metrics"]


def test_export_orders_csv_authenticated(brand: APIClient) -> None:
    resp = brand.get("/v1/reports/export/orders/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert 'filename="orders.csv"' in resp["Content-Disposition"]
    content = resp.content.decode("utf-8")
    assert "Order ID,Status,Brand,Creator" in content


def test_export_campaigns_csv_authenticated(brand: APIClient, campaign_payload: dict) -> None:
    brand.post("/v1/campaigns/", campaign_payload, format="json")
    resp = brand.get("/v1/reports/export/campaigns/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert 'filename="campaigns.csv"' in resp["Content-Disposition"]
    content = resp.content.decode("utf-8")
    assert "Campaign ID,Title,Platform" in content
    assert campaign_payload["title"] in content


def test_export_transactions_csv_authenticated(creator: APIClient) -> None:
    resp = creator.get("/v1/reports/export/transactions/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert 'filename="transactions.csv"' in resp["Content-Disposition"]
    content = resp.content.decode("utf-8")
    assert "Transaction ID,Type,Amount (Paise)" in content


def test_exports_unauthenticated_rejected(anon: APIClient) -> None:
    assert anon.get("/v1/reports/export/orders/").status_code == 401
    assert anon.get("/v1/reports/export/campaigns/").status_code == 401
    assert anon.get("/v1/reports/export/transactions/").status_code == 401
