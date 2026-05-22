from pydantic import BaseModel, Field


class EnterpriseAuthConfig(BaseModel):
    mode: str = "local"
    service_url: str = ""
    token_header: str = "X-Auth-Token"


class EnterpriseRbacConfig(BaseModel):
    service_url: str = ""


class EnterpriseConfig(BaseModel):
    enabled: bool = False
    auth: EnterpriseAuthConfig = Field(default_factory=EnterpriseAuthConfig)
    rbac: EnterpriseRbacConfig = Field(default_factory=EnterpriseRbacConfig)
