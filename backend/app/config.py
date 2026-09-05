"""Runtime configuration for the backend."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / ".." / "data"

DB_PATH = Path(os.environ.get("APP_DB_PATH", DATA_DIR / "app.db"))

INITIAL_CASH = float(os.environ.get("WALLET_INITIAL_CASH", "1000.0"))
DEFAULT_CURRENCY = os.environ.get("WALLET_CURRENCY", "USDC")

MAX_SINGLE_MARKET_EXPOSURE = float(os.environ.get("MAX_SINGLE_MARKET_EXPOSURE", "0.10"))
MAX_TOTAL_EXPOSURE = float(os.environ.get("MAX_TOTAL_EXPOSURE", "0.40"))
MAX_KELLY_FRACTION = float(os.environ.get("MAX_KELLY_FRACTION", "0.25"))
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.02"))
VERIFY_CONFIDENCE_THRESHOLD = float(os.environ.get("VERIFY_CONFIDENCE_THRESHOLD", "0.55"))
VERIFY_PRICE_TOLERANCE = float(os.environ.get("VERIFY_PRICE_TOLERANCE", "0.15"))

RAG_EMBED_DIM = int(os.environ.get("RAG_EMBED_DIM", "256"))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
RAG_CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "300"))
RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "30"))

PAPER_MIN_ORDER_QTY = 1
PAPER_MAX_ORDER_QTY = 10000
PAPER_FEE_RATE = float(os.environ.get("PAPER_FEE_RATE", "0.0"))

AGENT_VERSION = "1.0.0"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.is_absolute():
        raise RuntimeError("APP_DB_PATH must be absolute")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)