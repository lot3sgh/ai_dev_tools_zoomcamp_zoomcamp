"""Configuration: environment loading and database settings.

Values come from the environment; a `.env` file at the repo root is loaded
first (dev convenience). The environment always wins.
"""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    """The repository root: the nearest ancestor holding pyproject.toml.

    The source lives under backend/src/ in dev and is copied to /app/src in the
    container (no pyproject there), so the fallback is the working directory — which
    the Docker images set to /app, where .env and frontend/ are mounted.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


REPO_ROOT = _repo_root()
DEFAULT_SA_KEY = REPO_ROOT / "wise-weaver-509208-b9-54cec59c6c1d.json"


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from an env file without overriding existing env."""
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("\"'")
        if key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    load_env_file()


def _first_env(*names: str, default: str) -> str:
    """First set variable among *names* (libpq-style wins over POSTGRES_*)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def db_env() -> dict[str, str]:
    # Accept both the libpq names (PGPASSWORD/PGUSER/PGDATABASE) and the
    # POSTGRES_* names used by .env / docker-compose (POSTGRES_PASSWORD & co.).
    return {
        "dbname": _first_env("PGDATABASE", "POSTGRES_DB", default="health_pipeline"),
        "host": _first_env("PGHOST", default="localhost"),
        "port": _first_env("PGPORT", default="5433"),
        "user": _first_env("PGUSER", "POSTGRES_USER", default="pipeline"),
        "password": _first_env("PGPASSWORD", "POSTGRES_PASSWORD", default="pipeline_dev_password"),
    }


def dashboard_password() -> str | None:
    """Password for the read-only analytics login; None disables provisioning."""
    return os.environ.get("DASHBOARD_DB_PASSWORD")


def chatbot_password() -> str | None:
    """Password for the chatbot role (Semantic Layer, read-only); None disables provisioning."""
    return os.environ.get("CHATBOT_DB_PASSWORD")


def llm_provider() -> str:
    """Provider label: "stub" selects the deterministic test provider; anything else is the
    OpenAI-compatible seam selected by LLM_BASE_URL/LLM_MODEL/LLM_API_KEY."""
    return os.environ.get("LLM_PROVIDER", "").strip()


def llm_base_url() -> str | None:
    """OpenAI-compatible base URL (e.g. Ollama /v1, BMF, the OpenCode gateway)."""
    value = os.environ.get("LLM_BASE_URL", "").strip()
    return value or None


def llm_model() -> str | None:
    value = os.environ.get("LLM_MODEL", "").strip()
    return value or None


def llm_api_key() -> str | None:
    value = os.environ.get("LLM_API_KEY", "").strip()
    return value or None


def sa_key_path() -> Path | None:
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        p = Path(path)
        if not p.is_absolute():
            # Resolve relative paths against the repo root, not the CWD, so
            # the CLI works from any directory.
            p = REPO_ROOT / p
        return p if p.is_file() else None
    return DEFAULT_SA_KEY if DEFAULT_SA_KEY.is_file() else None
