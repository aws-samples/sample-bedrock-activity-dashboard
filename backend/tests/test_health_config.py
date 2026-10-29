"""Unit tests for GET /api/health/config's availableRegions field."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


class TestAvailableRegions:
    """availableRegions reflects CLOUDTRAIL_REGIONS with a single-region fallback."""

    def test_falls_back_to_aws_region_when_cloudtrail_regions_unset(self):
        with patch("app.routes.health.settings") as mock_settings:
            mock_settings.demo_mode = False
            mock_settings.app_name = "Bedrock Activity Dashboard"
            mock_settings.cognito_user_pool_id = ""
            mock_settings.cognito_client_id = ""
            mock_settings.cognito_domain = ""
            mock_settings.cognito_region = ""
            mock_settings.cognito_identity_provider = ""
            mock_settings.aws_region = "us-west-2"
            mock_settings.cloudtrail_regions = ""
            resp = client.get("/api/health/config")
            assert resp.status_code == 200
            assert resp.json()["availableRegions"] == ["us-west-2"]

    def test_parses_multi_region_list(self):
        with patch("app.routes.health.settings") as mock_settings:
            mock_settings.demo_mode = False
            mock_settings.app_name = "Bedrock Activity Dashboard"
            mock_settings.cognito_user_pool_id = ""
            mock_settings.cognito_client_id = ""
            mock_settings.cognito_domain = ""
            mock_settings.cognito_region = ""
            mock_settings.cognito_identity_provider = ""
            mock_settings.aws_region = "us-west-2"
            mock_settings.cloudtrail_regions = "us-west-2,us-east-1"
            resp = client.get("/api/health/config")
            assert resp.status_code == 200
            assert resp.json()["availableRegions"] == ["us-west-2", "us-east-1"]
