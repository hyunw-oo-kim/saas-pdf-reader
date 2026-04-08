from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "SaaS PDF Reader"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./pdf_reader.db"

    # Local File Storage (Azure Blob Storage 대체)
    local_storage_path: str = "./storage"
    backend_base_url: str = "http://localhost:8000"

    # Auth0 OIDC
    auth0_domain: str = ""
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    auth0_audience: str = ""

    # JWKS Cache
    jwks_cache_ttl_seconds: int = 3600  # 1 hour

    # JWT (내부 토큰 발급용)
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # SAS Token (파일 서빙 URL 만료)
    sas_token_expire_minutes: int = 15

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Azure AI Document Intelligence
    azure_doc_intelligence_endpoint: str = ""
    azure_doc_intelligence_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
