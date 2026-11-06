from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    # Exact-match list; browsers treat localhost and 127.0.0.1 as distinct origins.
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
        if origin.strip()
    )
    dolt_host: str = os.getenv("DOLT_HOST", "127.0.0.1")
    dolt_port: int = int(os.getenv("DOLT_PORT", "3306"))
    dolt_user: str = os.getenv("DOLT_USER", "root")
    dolt_password: str = os.getenv("DOLT_PASSWORD", os.getenv("DOLT_ROOT_PASSWORD", "dolt-demo-password"))
    dolt_database: str = os.getenv("DOLT_DATABASE", "malleable")
    # The only live model is Muse Glimmer, served locally by llama.cpp
    # (OpenAI-compatible). LLM_MODE selects it ("local", default) or the
    # deterministic replay model ("demo", no model download). Normalized
    # below: anything that isn't "demo" means the local Glimmer model.
    llm_mode: str = os.getenv("LLM_MODE", "local").strip().lower()
    local_base_url: str = os.getenv("LOCAL_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    local_model: str = os.getenv("LOCAL_MODEL", "muse-glimmer-30b")
    local_api_key: str = os.getenv("LOCAL_API_KEY", "")
    dolthub_remote: str = os.getenv("DOLTHUB_REMOTE", "").strip()


settings = Settings()
