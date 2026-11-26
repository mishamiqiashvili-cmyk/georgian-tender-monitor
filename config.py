"""Configuration loader for Georgian State Procurement monitor."""

import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    cpv_codes: list[str]
    state_file: str = "state.json"
    lookback_days: int = 2
    batch_size: int = 10
    request_timeout: int = 25
    max_retries: int = 3
    max_pages_per_batch: int = 3
    max_doc_bytes: int = 15 * 1024 * 1024
    dry_run: bool = False


def parse_cpv_codes(raw: str) -> list[str]:
    """Extract unique 8-digit CPV codes preserving order."""
    tokens = re.findall(r"\b\d{8}\b", raw or "")
    seen: set[str] = set()
    ordered: list[str] = []
    for code in tokens:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def load_config() -> Config:
    """Load and validate required settings from environment variables."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()
    cpv_raw = os.getenv("CPV_CODES", "").strip()

    if not bot_token:
        raise ValueError("BOT_TOKEN environment variable is required")
    if not chat_id:
        raise ValueError("CHAT_ID environment variable is required")

    cpv_codes = parse_cpv_codes(cpv_raw)
    if not cpv_codes:
        raise ValueError("CPV_CODES environment variable must contain at least one 8-digit CPV code")

    return Config(
        bot_token=bot_token,
        chat_id=chat_id,
        cpv_codes=cpv_codes,
        state_file=os.getenv("STATE_FILE", "state.json").strip() or "state.json",
        lookback_days=int(os.getenv("LOOKBACK_DAYS", "2")),
        batch_size=int(os.getenv("BATCH_SIZE", "10")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "25")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        max_pages_per_batch=int(os.getenv("MAX_PAGES_PER_BATCH", "3")),
        dry_run=os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes"},
    )
