"""Configuration and operational limits for MoneyTrail."""

import os
from pathlib import Path
from urllib.parse import urlparse
from pydantic import BaseModel, Field, field_validator

# Fixed operational limits from ARCHITECTURE.md
ZIP_MAX_COMPRESSED_BYTES = 20 * 1024 * 1024  # 20 MiB
ZIP_MAX_INFLATED_BYTES = 50 * 1024 * 1024    # 50 MiB
ZIP_MAX_FILES = 50
CSV_MAX_TOTAL_RECORDS = 100_000
PDF_MAX_PAGES = 100
DOC_MAX_BYTES = 10 * 1024 * 1024            # 10 MiB
QUESTION_MAX_CHARS = 2_000
OLLAMA_CONTEXT_TOKENS = 8_192
MODEL_OUTPUT_ALLOWANCE_TOKENS = 1_024
SQL_STATEMENT_TIMEOUT_SECONDS = 5


def _is_loopback(url_str: str) -> bool:
    try:
        parsed = urlparse(url_str)
        hostname = (parsed.hostname or "").lower()
        return hostname in {"127.0.0.1", "localhost", "::1"}
    except Exception:
        return False


class PilotSettings(BaseModel):
    """Permissive settings used for initial Phase 0 pilot discovery."""
    ollama_url: str = Field(default="http://127.0.0.1:11434")
    model: str = Field(default="qwen3:8b")
    model_digest: str = Field(default="")
    tokenizer: str = Field(default="Qwen/Qwen3-8B")
    tokenizer_revision: str = Field(default="")
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    embedding_revision: str = Field(default="")
    reranker_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L6-v2")
    reranker_revision: str = Field(default="")
    data_dir: Path = Field(default=Path("./data"))
    input_token_budget: int = Field(default=6000)
    max_tool_calls: int = Field(default=6)
    max_model_turns: int = Field(default=4)
    model_timeout_seconds: int = Field(default=60)
    run_timeout_seconds: int = Field(default=240)

    @classmethod
    def from_env(cls) -> "PilotSettings":
        data_dir_str = os.getenv("MONEYTRAIL_DATA_DIR", "./data")
        return cls(
            ollama_url=os.getenv("MONEYTRAIL_OLLAMA_URL", "http://127.0.0.1:11434"),
            model=os.getenv("MONEYTRAIL_MODEL", "qwen3:8b"),
            model_digest=os.getenv("MONEYTRAIL_MODEL_DIGEST", ""),
            tokenizer=os.getenv("MONEYTRAIL_MODEL_TOKENIZER", "Qwen/Qwen3-8B"),
            tokenizer_revision=os.getenv("MONEYTRAIL_TOKENIZER_REVISION", ""),
            embedding_model=os.getenv("MONEYTRAIL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            embedding_revision=os.getenv("MONEYTRAIL_EMBEDDING_REVISION", ""),
            reranker_model=os.getenv("MONEYTRAIL_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2"),
            reranker_revision=os.getenv("MONEYTRAIL_RERANKER_REVISION", ""),
            data_dir=Path(data_dir_str).resolve(),
            input_token_budget=int(os.getenv("MONEYTRAIL_INPUT_TOKEN_BUDGET", "6000")),
            max_tool_calls=int(os.getenv("MONEYTRAIL_MAX_TOOL_CALLS", "6")),
            max_model_turns=int(os.getenv("MONEYTRAIL_MAX_MODEL_TURNS", "4")),
            model_timeout_seconds=int(os.getenv("MONEYTRAIL_MODEL_TIMEOUT_SECONDS", "60")),
            run_timeout_seconds=int(os.getenv("MONEYTRAIL_RUN_TIMEOUT_SECONDS", "240")),
        )


class Settings(BaseModel):
    """Runtime application settings with strict validation."""
    database_url: str
    read_database_url: str
    owner_token: str
    ollama_url: str = Field(default="http://127.0.0.1:11434")
    model: str = Field(default="qwen3:8b")
    model_digest: str
    tokenizer: str = Field(default="Qwen/Qwen3-8B")
    tokenizer_revision: str
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    embedding_revision: str
    reranker_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L6-v2")
    reranker_revision: str
    data_dir: Path = Field(default=Path("./data"))
    input_token_budget: int = Field(default=6000)
    max_tool_calls: int = Field(default=6)
    max_model_turns: int = Field(default=4)
    model_timeout_seconds: int = Field(default=60)
    run_timeout_seconds: int = Field(default=240)

    @field_validator("owner_token")
    @classmethod
    def validate_owner_token(cls, v: str) -> str:
        if not v or len(v.strip()) < 32:
            raise ValueError("Invalid setting: MONEYTRAIL_OWNER_TOKEN must be at least 32 characters")
        return v.strip()

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v or not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("Invalid setting: MONEYTRAIL_DATABASE_URL must be a valid PostgreSQL DSN")
        return v.strip()

    @field_validator("read_database_url")
    @classmethod
    def validate_read_database_url(cls, v: str) -> str:
        if not v or not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("Invalid setting: MONEYTRAIL_READ_DATABASE_URL must be a valid PostgreSQL DSN")
        return v.strip()

    @field_validator("ollama_url")
    @classmethod
    def validate_ollama_url(cls, v: str) -> str:
        if not v:
            raise ValueError("Invalid setting: MONEYTRAIL_OLLAMA_URL cannot be empty")
        # Enforce loopback check unless explicitly approved
        allow_remote = os.getenv("MONEYTRAIL_ALLOW_REMOTE_OLLAMA", "false").lower() in ("true", "1")
        if not allow_remote and not _is_loopback(v):
            raise ValueError("Invalid setting: MONEYTRAIL_OLLAMA_URL must bind to loopback (127.0.0.1 or localhost)")
        return v.strip()

    @field_validator("model_digest", "tokenizer_revision", "embedding_revision", "reranker_revision")
    @classmethod
    def validate_required_revisions(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"Invalid setting: {info.field_name} must be locked and nonempty")
        return v.strip()

    @field_validator("input_token_budget", "max_tool_calls", "max_model_turns", "model_timeout_seconds", "run_timeout_seconds")
    @classmethod
    def validate_positive_budgets(cls, v: int, info) -> int:
        if v <= 0:
            raise ValueError(f"Invalid setting: {info.field_name} must be positive")
        return v

    @classmethod
    def from_env(cls) -> "Settings":
        errors = []
        db_url = os.getenv("MONEYTRAIL_DATABASE_URL", "")
        read_db_url = os.getenv("MONEYTRAIL_READ_DATABASE_URL", "")
        token = os.getenv("MONEYTRAIL_OWNER_TOKEN", "")
        digest = os.getenv("MONEYTRAIL_MODEL_DIGEST", "")
        tok_rev = os.getenv("MONEYTRAIL_TOKENIZER_REVISION", "")
        emb_rev = os.getenv("MONEYTRAIL_EMBEDDING_REVISION", "")
        rer_rev = os.getenv("MONEYTRAIL_RERANKER_REVISION", "")

        if not db_url:
            errors.append("MONEYTRAIL_DATABASE_URL is missing")
        if not read_db_url:
            errors.append("MONEYTRAIL_READ_DATABASE_URL is missing")
        if not token or len(token) < 32:
            errors.append("MONEYTRAIL_OWNER_TOKEN is missing or fewer than 32 characters")
        if not digest:
            errors.append("MONEYTRAIL_MODEL_DIGEST is missing")
        if not tok_rev:
            errors.append("MONEYTRAIL_TOKENIZER_REVISION is missing")
        if not emb_rev:
            errors.append("MONEYTRAIL_EMBEDDING_REVISION is missing")
        if not rer_rev:
            errors.append("MONEYTRAIL_RERANKER_REVISION is missing")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return cls(
            database_url=db_url,
            read_database_url=read_db_url,
            owner_token=token,
            ollama_url=os.getenv("MONEYTRAIL_OLLAMA_URL", "http://127.0.0.1:11434"),
            model=os.getenv("MONEYTRAIL_MODEL", "qwen3:8b"),
            model_digest=digest,
            tokenizer=os.getenv("MONEYTRAIL_MODEL_TOKENIZER", "Qwen/Qwen3-8B"),
            tokenizer_revision=tok_rev,
            embedding_model=os.getenv("MONEYTRAIL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            embedding_revision=emb_rev,
            reranker_model=os.getenv("MONEYTRAIL_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2"),
            reranker_revision=rer_rev,
            data_dir=Path(os.getenv("MONEYTRAIL_DATA_DIR", "./data")).resolve(),
            input_token_budget=int(os.getenv("MONEYTRAIL_INPUT_TOKEN_BUDGET", "6000")),
            max_tool_calls=int(os.getenv("MONEYTRAIL_MAX_TOOL_CALLS", "6")),
            max_model_turns=int(os.getenv("MONEYTRAIL_MAX_MODEL_TURNS", "4")),
            model_timeout_seconds=int(os.getenv("MONEYTRAIL_MODEL_TIMEOUT_SECONDS", "60")),
            run_timeout_seconds=int(os.getenv("MONEYTRAIL_RUN_TIMEOUT_SECONDS", "240")),
        )


class SetupSettings(BaseModel):
    """Admin-only settings used by bootstrap and migrate CLI."""
    admin_database_url: str
    postgres_user: str = "moneytrail_admin"
    postgres_db: str = "moneytrail"
    postgres_password: str = ""

    @classmethod
    def from_env(cls) -> "SetupSettings":
        admin_url = os.getenv("MONEYTRAIL_ADMIN_DATABASE_URL", "")
        if not admin_url:
            raise ValueError("MONEYTRAIL_ADMIN_DATABASE_URL is missing")
        return cls(
            admin_database_url=admin_url,
            postgres_user=os.getenv("POSTGRES_USER", "moneytrail_admin"),
            postgres_db=os.getenv("POSTGRES_DB", "moneytrail"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
        )


def generate_local_env(base_dir: Path = Path(".")) -> tuple[str, str]:
    """Generate separate .env and .env.setup files if they do not exist."""
    import secrets

    env_path = base_dir / ".env"
    setup_path = base_dir / ".env.setup"

    created = []

    if not env_path.exists():
        owner_token = secrets.token_urlsafe(32)
        writer_pw = secrets.token_urlsafe(32)
        reader_pw = secrets.token_urlsafe(32)

        env_content = f"""# Runtime MONEYTRAIL configuration (strictly no admin credentials)
MONEYTRAIL_DATABASE_URL=postgresql://moneytrail_writer:{writer_pw}@127.0.0.1:5432/moneytrail
MONEYTRAIL_READ_DATABASE_URL=postgresql://moneytrail_reader:{reader_pw}@127.0.0.1:5432/moneytrail
MONEYTRAIL_OWNER_TOKEN={owner_token}
MONEYTRAIL_OLLAMA_URL=http://127.0.0.1:11434
MONEYTRAIL_MODEL=qwen3:8b
MONEYTRAIL_MODEL_DIGEST=
MONEYTRAIL_MODEL_TOKENIZER=Qwen/Qwen3-8B
MONEYTRAIL_TOKENIZER_REVISION=
MONEYTRAIL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
MONEYTRAIL_EMBEDDING_REVISION=
MONEYTRAIL_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L6-v2
MONEYTRAIL_RERANKER_REVISION=
MONEYTRAIL_DATA_DIR=./data
MONEYTRAIL_INPUT_TOKEN_BUDGET=6000
MONEYTRAIL_MAX_TOOL_CALLS=6
MONEYTRAIL_MAX_MODEL_TURNS=4
MONEYTRAIL_MODEL_TIMEOUT_SECONDS=60
MONEYTRAIL_RUN_TIMEOUT_SECONDS=240
"""
        env_path.write_text(env_content, encoding="utf-8")
        created.append(str(env_path.name))

    if not setup_path.exists():
        admin_pw = secrets.token_urlsafe(32)
        setup_content = f"""# Setup-only PostgreSQL credentials for bootstrap/migration
POSTGRES_USER=moneytrail_admin
POSTGRES_DB=moneytrail
POSTGRES_PASSWORD={admin_pw}
MONEYTRAIL_ADMIN_DATABASE_URL=postgresql://moneytrail_admin:{admin_pw}@127.0.0.1:5432/moneytrail
"""
        setup_path.write_text(setup_content, encoding="utf-8")
        created.append(str(setup_path.name))

    return str(env_path.name), str(setup_path.name)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "generate-env":
        f1, f2 = generate_local_env(Path("."))
        print(f"Verified configuration files: {f1}, {f2}")

