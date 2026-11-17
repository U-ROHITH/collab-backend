import pytest
from drf_spectacular.generators import SchemaGenerator

pytestmark = pytest.mark.django_db


def test_creator_openapi_schema_generation() -> None:
    generator = SchemaGenerator()
    schema = generator.get_schema(request=None, public=True)

    schemas = schema["components"]["schemas"]

    # 1. Platform output schemas exist with expected fields
    assert "PlatformStatsOutput" in schemas
    stats_out_props = schemas["PlatformStatsOutput"]["properties"]
    assert "handle" in stats_out_props
    assert "url" in stats_out_props
    assert "followersCount" in stats_out_props
    assert "avgViews" in stats_out_props
    assert "engagementRate" in stats_out_props
    assert "verified" in stats_out_props

    assert "PlatformsOutput" in schemas
    platforms_out_props = schemas["PlatformsOutput"]["properties"]
    assert "instagram" in platforms_out_props
    assert "youtube" in platforms_out_props

    # 2. CreatorCardOutput references PlatformsOutput
    assert "CreatorCardOutput" in schemas
    card_platforms = schemas["CreatorCardOutput"]["properties"]["platforms"]
    assert card_platforms.get("allOf", [{}])[0].get("$ref") == "#/components/schemas/PlatformsOutput"

    # 3. CreatorProfileInputRequest documents oneOf for platforms
    input_schema = schemas.get("PatchedCreatorProfileInputRequest") or schemas.get("CreatorProfileInputRequest")
    assert input_schema is not None
    props = input_schema["properties"]

    assert "instagram" in props
    assert "oneOf" in props["instagram"]
    assert any("$ref" in item and "PlatformStatsInput" in item["$ref"] for item in props["instagram"]["oneOf"])
    assert any(item.get("type") == "string" for item in props["instagram"]["oneOf"])

    assert "youtube" in props
    assert "oneOf" in props["youtube"]

    assert "platforms" in props
    platforms_input_props = props["platforms"]["properties"]
    assert "instagram" in platforms_input_props
    assert "oneOf" in platforms_input_props["instagram"]
    assert "youtube" in platforms_input_props
    assert "oneOf" in platforms_input_props["youtube"]
