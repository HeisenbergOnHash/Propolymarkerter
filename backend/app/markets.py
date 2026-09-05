"""Markets: data model, persistence, sample dataset and a data source abstraction.

The data source interface lets a real Polymarket/CCData integration be dropped in
later; by default a deterministic sample provider feeds the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from . import config, db

VALID_STATUSES = {"open", "closed", "pending"}


class Outcome(BaseModel):
    id: str
    name: str
    price: float = Field(ge=0.0, le=1.0)
    last_price: float = Field(ge=0.0, le=1.0)
    volume: float = 0.0


class Market(BaseModel):
    id: str
    question: str
    description: str = ""
    category: str = "crypto"
    status: str = "open"
    liquidity: float = 0.0
    volume: float = 0.0
    volume_24h: float = 0.0
    end_date: str | None = None
    source: str = "sample"
    tags: list[str] = field(default_factory=list)
    resolution: str | None = None
    outcomes: list[Outcome] = field(default_factory=list)

    def outcome(self, outcome_id: str) -> Outcome | None:
        return next((o for o in self.outcomes if o.id == outcome_id), None)

    @property
    def best_yes_price(self) -> float:
        if not self.outcomes:
            return 0.5
        return max(o.price for o in self.outcomes)


@dataclass
class EvidenceRef:
    market_id: str
    outcome_id: str
    source: str
    statement: str
    stance: float  # +1 supportive, -1 against, 0 neutral
    date: str
    weight: float = 1.0


class MarketDataSource:
    """Abstraction for pulling markets and evidence.

    The default implementation serves the seeded sample dataset; swap
    ``get_markets``/``get_evidence``/``get_price_feed`` for a live provider.
    """

    def get_markets(self, refresh: bool = False) -> list[Market]:
        return list_markets(status="open")

    def get_evidence(self, market: Market) -> list[EvidenceRef]:
        return evidence_for_market(market.id)

    def get_price_feed(self, market_id: str) -> list[dict[str, Any]]:
        return simulate_price_feed(market_id, points=30)


# ---------------------------------------------------------------------------
# Sample dataset
# ---------------------------------------------------------------------------

SAMPLE_MARKETS = [
    {
        "id": "btc-100k-2026",
        "question": "Will Bitcoin exceed $100,000 before 2027?",
        "description": "BTC spot crosses 100k before end of 2026.",
        "category": "crypto",
        "status": "open",
        "liquidity": 312000,
        "volume": 1840000,
        "volume_24h": 142000,
        "end_date": "2026-12-31T23:59:59Z",
        "source": "sample",
        "tags": ["crypto", "bitcoin", "macro"],
    },
    {
        "id": "fed-rate-cut-dec2026",
        "question": "Will the Fed cut rates at the December 2026 FOMC meeting?",
        "description": "FOMC rate decision for December 2026.",
        "category": "macro",
        "status": "open",
        "liquidity": 198000,
        "volume": 962000,
        "volume_24h": 71000,
        "end_date": "2026-12-18T23:59:59Z",
        "source": "sample",
        "tags": ["macro", "rates", "fed"],
    },
    {
        "id": "eth-4k-2026",
        "question": "Will Ethereum exceed $4,000 before 2027?",
        "description": "ETH price crosses 4k by end of 2026.",
        "category": "crypto",
        "status": "open",
        "liquidity": 156000,
        "volume": 743000,
        "volume_24h": 44000,
        "end_date": "2026-12-31T23:59:59Z",
        "source": "sample",
        "tags": ["crypto", "ethereum"],
    },
    {
        "id": "ai-market-cap-2026",
        "question": "Will the combined AI company market cap exceed $4T in 2026?",
        "description": "Top AI firms' combined market cap crosses 4 trillion dollars.",
        "category": "equities",
        "status": "open",
        "liquidity": 88000,
        "volume": 451000,
        "volume_24h": 29000,
        "end_date": "2026-12-31T23:59:59Z",
        "source": "sample",
        "tags": ["equities", "ai"],
    },
    {
        "id": "spacex-starship-2026",
        "question": "Will SpaceX complete an orbital Starship launch in 2026?",
        "description": "Starship reaches orbit before end of 2026.",
        "category": "space",
        "status": "open",
        "liquidity": 52000,
        "volume": 188000,
        "volume_24h": 12500,
        "end_date": "2026-12-31T23:59:59Z",
        "source": "sample",
        "tags": ["space", "spacex"],
    },
    {
        "id": "btc-100k-2026-closed",
        "question": "Will Bitcoin exceed $100,000 in Q1 2026?",
        "description": "Already resolved, kept to exercise settlement.",
        "category": "crypto",
        "status": "closed",
        "liquidity": 0,
        "volume": 821000,
        "volume_24h": 0,
        "end_date": "2026-03-31T23:59:59Z",
        "source": "sample",
        "tags": ["crypto", "bitcoin", "resolved"],
        "resolution": "YES",
    },
]


def seed_markets() -> None:
    existing = db.fetch_one("SELECT COUNT(*) AS c FROM markets")
    if existing and existing["c"] > 0:
        return
    for raw in SAMPLE_MARKETS:
        tags = raw.get("tags", [])
        db.execute(
            """
            INSERT INTO markets
              (id, question, description, category, status, liquidity, volume,
               volume_24h, end_date, source, tags, resolution, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                raw["id"], raw["question"], raw["description"], raw["category"],
                raw["status"], raw["liquidity"], raw["volume"], raw["volume_24h"],
                raw.get("end_date"), raw["source"], db.dumps(tags),
                raw.get("resolution"), db.utc_now(),
            ),
        )
        yes_id = f"{raw['id']}-YES"
        no_id = f"{raw['id']}-NO"
        yes_price, no_price = _seed_prices(raw["id"])
        db.execute_many(
            "INSERT INTO outcomes (id, market_id, name, price, last_price, volume) "
            "VALUES (?,?,?,?,?,?)",
            [
                (yes_id, raw["id"], "YES", yes_price, yes_price, raw["volume"] * 0.62),
                (no_id, raw["id"], "NO", no_price, no_price, raw["volume"] * 0.38),
            ],
        )


def _seed_prices(market_id: str) -> tuple[float, float]:
    base = {
        "btc-100k-2026": 0.47,
        "fed-rate-cut-dec2026": 0.61,
        "eth-4k-2026": 0.33,
        "ai-market-cap-2026": 0.42,
        "spacex-starship-2026": 0.38,
        "btc-100k-2026-closed": 1.0,
    }
    yes = base.get(market_id, 0.5)
    return round(yes, 3), round(1 - yes, 3)


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def list_markets(status: str | None = None, category: str | None = None,
                 min_volume_24h: float | None = None,
                 min_liquidity: float | None = None) -> list[Market]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    if min_volume_24h is not None:
        where.append("volume_24h >= ?")
        params.append(min_volume_24h)
    if min_liquidity is not None:
        where.append("liquidity >= ?")
        params.append(min_liquidity)
    sql = "SELECT * FROM markets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY volume_24h DESC"
    rows = db.fetch_all(sql, tuple(params))
    markets = [_market_from_row(r) for r in rows]
    _attach_outcomes(markets)
    return markets


def get_market(market_id: str) -> Market | None:
    row = db.fetch_one("SELECT * FROM markets WHERE id = ?", (market_id,))
    if row is None:
        return None
    market = _market_from_row(row)
    _attach_outcomes([market])
    return market


def _market_from_row(row: dict[str, Any]) -> Market:
    return Market(
        id=row["id"], question=row["question"], description=row["description"] or "",
        category=row["category"], status=row["status"], liquidity=row["liquidity"],
        volume=row["volume"], volume_24h=row["volume_24h"], end_date=row["end_date"],
        source=row["source"], tags=db.loads(row["tags"], []),
        resolution=row["resolution"],
    )


def _attach_outcomes(markets: list[Market]) -> None:
    if not markets:
        return
    ids = [m.id for m in markets]
    placeholders = ",".join("?" * len(ids))
    rows = db.fetch_all(
        f"SELECT * FROM outcomes WHERE market_id IN ({placeholders}) ORDER BY rowid",
        tuple(ids),
    )
    by_market: dict[str, list[Outcome]] = {}
    for r in rows:
        by_market.setdefault(r["market_id"], []).append(
            Outcome(id=r["id"], name=r["name"], price=r["price"],
                    last_price=r["last_price"], volume=r["volume"])
        )
    for m in markets:
        m.outcomes = by_market.get(m.id, [])


def get_outcome(market_id: str, outcome_id: str) -> tuple[Market, Outcome] | None:
    market = get_market(market_id)
    if market is None:
        return None
    outcome = market.outcome(outcome_id)
    if outcome is None:
        return None
    return market, outcome


def update_outcome_price(market_id: str, outcome_id: str, price: float) -> None:
    price = max(0.0, min(1.0, price))
    outcome = db.fetch_one(
        "SELECT price FROM outcomes WHERE id = ? AND market_id = ?",
        (outcome_id, market_id),
    )
    if outcome is None:
        raise ValueError(f"unknown outcome {outcome_id}")
    db.execute(
        "UPDATE outcomes SET last_price = price, price = ? WHERE id = ?",
        (price, outcome_id),
    )
    db.execute("UPDATE markets SET updated_at = ? WHERE id = ?",
               (db.utc_now(), market_id))


def tick_market(market_id: str, price: float) -> None:
    market = get_market(market_id)
    if market is None or not market.outcomes:
        return
    no = min(1.0, max(0.0, 1 - price))
    update_outcome_price(market_id, market.outcomes[0].id, round(price, 3))
    if len(market.outcomes) > 1:
        update_outcome_price(market_id, market.outcomes[1].id, round(no, 3))


def close_market(market_id: str, resolution: str) -> Market:
    market = get_market(market_id)
    if market is None:
        raise ValueError(f"unknown market {market_id}")
    db.execute(
        "UPDATE markets SET status='closed', resolution=?, updated_at=? WHERE id=?",
        (resolution.upper(), db.utc_now(), market_id),
    )
    return get_market(market_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Evidence + price feed (deterministic per-market knowledge for research agents)
# ---------------------------------------------------------------------------

EVIDENCE_CORPUS = {
    "btc-100k-2026": [
        EvidenceRef("btc-100k-2026", "btc-100k-2026-YES", "miner-flows", "Bitcoin supply on exchanges is at a multi-year low, reducing sell pressure.", 1, "2026-08-01", 0.9),
        EvidenceRef("btc-100k-2026", "btc-100k-2026-YES", "etf-flows", "Spot BTC ETF net inflows averaged $1.2B/week over the last quarter.", 1, "2026-08-20", 0.95),
        EvidenceRef("btc-100k-2026", "btc-100k-2026-NO", "rates", "Persistent high rates weigh on risk assets through year end.", -1, "2026-07-30", 0.5),
        EvidenceRef("btc-100k-2026", "btc-100k-2026-YES", "derivatives", "Funding rates remain positive without being overheated, indicating healthy leverage.", 1, "2026-08-15", 0.6),
    ],
    "fed-rate-cut-dec2026": [
        EvidenceRef("fed-rate-cut-dec2026", "fed-rate-cut-dec2026-YES", "cpi", "Core CPI cooled to 2.9% YoY, trending toward target.", 1, "2026-08-10", 0.9),
        EvidenceRef("fed-rate-cut-dec2026", "fed-rate-cut-dec2026-YES", "cme-fedwatch", "Fed funds futures price an 89% probability of a December cut.", 1, "2026-08-25", 0.85),
        EvidenceRef("fed-rate-cut-dec2026", "fed-rate-cut-dec2026-NO", "hawks", "Multiple FOMC participants signal patience on further easing.", -1, "2026-08-12", 0.55),
    ],
    "eth-4k-2026": [
        EvidenceRef("eth-4k-2026", "eth-4k-2026-YES", "staking", "Network staking yield and burn are deflationary at current activity.", 1, "2026-08-05", 0.55),
        EvidenceRef("eth-4k-2026", "eth-4k-2026-NO", "compression", "ETH underperforms BTC in prevailing risk-on rotations.", -1, "2026-08-18", 0.7),
    ],
    "ai-market-cap-2026": [
        EvidenceRef("ai-market-cap-2026", "ai-market-cap-2026-YES", "earnings", "CapEx guidance from hyperscalers was raised again this quarter.", 1, "2026-08-22", 0.8),
        EvidenceRef("ai-market-cap-2026", "ai-market-cap-2026-YES", "adoption", "Enterprise AI adoption survey shows aggressive 2026 rollouts.", 1, "2026-08-01", 0.6),
        EvidenceRef("ai-market-cap-2026", "ai-market-cap-2026-NO", "valuation", "Sell-side flags crowded positioning and rich multiples.", -1, "2026-08-14", 0.6),
    ],
    "spacex-starship-2026": [
        EvidenceRef("spacex-starship-2026", "spacex-starship-2026-YES", "flights", "Starfactory is producing boosters at rate exceeding flight cadence goals.", 1, "2026-07-25", 0.7),
        EvidenceRef("spacex-starship-2026", "spacex-starship-2026-YES", "licenses", "FAA license modifications granted for launch cadence increase.", 1, "2026-08-09", 0.65),
        EvidenceRef("spacex-starship-2026", "spacex-starship-2026-NO", "schedule", "Historical slip risk is high for ambitious planetary launch timelines.", -1, "2026-08-03", 0.6),
    ],
}


def evidence_for_market(market_id: str) -> list[EvidenceRef]:
    base = [r for r in EVIDENCE_CORPUS.get(market_id, [])]
    return list(base)


def simulate_price_feed(market_id: str, points: int = 30) -> list[dict[str, Any]]:
    market = get_market(market_id)
    if market is None or not market.outcomes:
        return []
    base = market.outcomes[0].price
    import math

    series = []
    drift = 0.0
    for i in range(points):
        wave = 0.02 * math.sin(i / 3.0)
        noise = ((i * 7919) % 101 - 50) / 100 * 0.012
        drift += 0.001 * ((i * 104729) % 7 - 3)
        price = max(0.02, min(0.98, base + wave + noise + drift))
        series.append({
            "t": i, "outcome_id": market.outcomes[0].id,
            "price": round(price, 3),
        })
    return series


def seed_data() -> None:
    seed_markets()