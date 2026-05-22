import json
from qwenpaw.enterprise.config import EnterpriseConfig, EnterpriseAuthConfig, EnterpriseRbacConfig


def test_enterprise_config_defaults():
    cfg = EnterpriseConfig()
    assert cfg.enabled is False
    assert cfg.auth.mode == "local"
    assert cfg.auth.service_url == ""
    assert cfg.rbac.service_url == ""


def test_enterprise_config_enabled_with_external_auth():
    cfg = EnterpriseConfig(
        enabled=True,
        auth=EnterpriseAuthConfig(
            mode="external",
            service_url="http://auth-service:8080",
        ),
        rbac=EnterpriseRbacConfig(
            service_url="http://rbac-service:8080",
        ),
    )
    assert cfg.enabled is True
    assert cfg.auth.mode == "external"
    assert cfg.auth.service_url == "http://auth-service:8080"
    assert cfg.rbac.service_url == "http://rbac-service:8080"


def test_enterprise_config_serialization():
    cfg = EnterpriseConfig(enabled=True)
    data = cfg.model_dump()
    assert data["enabled"] is True
    # Round-trip
    cfg2 = EnterpriseConfig(**data)
    assert cfg2.enabled is True
