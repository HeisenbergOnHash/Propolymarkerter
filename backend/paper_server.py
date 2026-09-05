"""
Propolymarketer Paper Trading Engine
====================================
FastAPI server with paper trading endpoints + automated execution loop.
Builds on existing: markets.py, wallet.py, db.py, config.py
"""
from __future__ import annotations
import os, sys, json, asyncio, logging, time
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path

os.environ.setdefault("APP_DB_PATH", "/home/hermes/propolymarketer/data/app.db")
sys.path.insert(0, "/home/hermes/propolymarketer/backend")

import httpx
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

from app.config import config as app_config
from app.db import init_db, fetch_all, fetch_one, execute, execute_many
from app.markets import Market, Outcome, MarketDataSource, seed_data, list_markets, get_market
from app.wallet import create_account, get_account, get_balance, deposit, withdraw, _apply, ledger as ledger_entries, settle
from app.markets import tick_market, update_outcome_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("paper-trading")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class OrderCreate(BaseModel):
    account_id: str
    market_id: str
    outcome_id: str
    side: str  # "YES" or "NO"
    qty: float = Field(ge=1, le=10000)
    limit_price: float = Field(ge=0.01, le=0.99)

class OrderResponse(BaseModel):
    order_id: str
    status: str
    filled_qty: float = 0.0
    avg_price: float = 0.0
    pnl: float = 0.0

class PositionResponse(BaseModel):
    account_id: str
    market_id: str
    outcome_id: str
    side: str
    shares: float
    avg_cost: float
    unrealized_pnl: float = 0.0
    status: str = "open"

class PnLReport(BaseModel):
    account_id: str
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    starting_cash: float
    current_cash: float
    trades: list[dict[str, Any]] = []

class TradeRecord(BaseModel):
    timestamp: str
    market_id: str
    question: str
    side: str
    qty: float
    price: float
    pnl: float
    cash_after: float

# ---------------------------------------------------------------------------
# Paper Trading Engine Core
# ---------------------------------------------------------------------------
@dataclass
class PaperTrade:
    timestamp: str
    market_id: str
    question: str
    side: str
    qty: float
    price: float
    pnl: float = 0.0
    cash_after: float = 0.0

class PaperTradingEngine:
    def __init__(self):
        self.init_db()
        self.account_id: Optional[str] = None
        self.trades: list[PaperTrade] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self._running = False
        self._log_path = Path("/home/hermes/propolymarketer/paper_trades.log")
        
    def init_db(self):
        init_db()
        seed_data()
        # Create paper trading account with $50
        if not self.account_id:
            acc = create_account("paper_trader", "USDC")
            # Override to $50
            acc_id = acc["id"]
            # Reset cash to $50
            execute("UPDATE accounts SET cash = 50.0 WHERE id = ?", (acc_id,))
            self.account_id = acc_id
            logger.info(f"Paper trading account created: {acc_id} with $50.00 USDC")
        
    def get_account(self) -> dict[str, Any] | None:
        return get_account(self.account_id)
    
    def get_balance(self) -> float:
        return get_balance(self.account_id)
    
    def get_positions(self) -> list[dict[str, Any]]:
        rows = fetch_all(
            "SELECT * FROM positions WHERE account_id = ? AND status = 'open'",
            (self.account_id,)
        )
        return rows
    
    def get_open_orders(self) -> list[dict[str, Any]]:
        rows = fetch_all(
            "SELECT * FROM orders WHERE account_id = ? AND status = 'open'",
            (self.account_id,)
        )
        return rows
    
    def get_ledger(self) -> list[dict[str, Any]]:
        return ledger_entries(self.account_id, limit=100)
    
    def place_order(self, market_id: str, outcome_id: str, side: str, qty: float, limit_price: float) -> dict[str, Any]:
        """Place a paper order — immediately fills at limit_price if cash permits."""
        account = self.get_account()
        if not account:
            raise ValueError("Account not found")
        
        cash = account["cash"]
        cost = qty * limit_price
        
        if cost > cash:
            raise ValueError(f"Insufficient funds: need ${cost:.2f}, have ${cash:.2f}")
        
        # Check position exposure limit
        max_exposure = float(os.environ.get("MAX_SINGLE_MARKET_EXPOSURE", "0.10"))
        total_exposure = float(os.environ.get("MAX_TOTAL_EXPOSURE", "0.40"))
        current_exposure = sum(
            p.get("shares", 0) * p.get("avg_cost", 0) 
            for p in self.positions.values()
        )
        if cost > (total_exposure * 50.0):  # 40% of $50 = $20 max total
            raise ValueError(f"Exposure limit exceeded: ${cost:.2f} would exceed {int(total_exposure*100)}% portfolio limit")
        
        order_id = f"order_{int(time.time())}_{market_id}"
        # Fill immediately at limit price
        position_key = f"{market_id}_{outcome_id}"
        
        # Check existing position
        existing = self.positions.get(position_key)
        if existing:
            # Average down/up
            total_shares = existing["shares"] + qty
            avg_cost = (existing["shares"] * existing["avg_cost"] + qty * limit_price) / total_shares
            existing["shares"] = total_shares
            existing["avg_cost"] = round(avg_cost, 4)
            existing["status"] = "open"
        else:
            self.positions[position_key] = {
                "account_id": self.account_id,
                "market_id": market_id,
                "outcome_id": outcome_id,
                "side": side,
                "shares": qty,
                "avg_cost": round(limit_price, 4),
                "status": "open"
            }
            db_entry = dict(self.positions[position_key])
            db_entry["id"] = f"pos_{order_id}"
            execute(
                "INSERT INTO positions (id, account_id, market_id, outcome_id, shares, avg_cost, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (db_entry["id"], db_entry["account_id"], db_entry["market_id"], db_entry["outcome_id"],
                 db_entry["shares"], db_entry["avg_cost"], db_entry["status"], datetime.now(timezone.utc).isoformat())
            )
        
        # Deduct cash
        execute("UPDATE accounts SET cash = cash - ? WHERE id = ?", (cost, self.account_id))
        
        # Record order
        self.orders[order_id] = {
            "id": order_id, "account_id": self.account_id, "market_id": market_id,
            "outcome_id": outcome_id, "side": side, "qty": qty, "limit_price": limit_price,
            "status": "filled", "filled_qty": qty, "created_at": datetime.now(timezone.utc).isoformat(),
            "filled_at": datetime.now(timezone.utc).isoformat()
        }
        execute(
            "INSERT OR REPLACE INTO orders (id, account_id, market_id, outcome_id, side, qty, limit_price, status, filled_qty, created_at, filled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, self.account_id, market_id, outcome_id, side, qty, limit_price, "filled", qty,
             self.orders[order_id]["created_at"], self.orders[order_id]["filled_at"])
        )
        
        # Record trade
        trade_pnl = 0.0  # Unrealized until settlement
        trade = PaperTrade(
            timestamp=datetime.now(timezone.utc).isoformat(),
            market_id=market_id,
            question="",
            side=side,
            qty=qty,
            price=limit_price,
            pnl=0.0,
            cash_after=self.get_balance()
        )
        self.trades.append(trade)
        
        # Log
        self._log_trade(trade)
        logger.info(f"ORDER FILLED: {side} {qty} @ ${limit_price:.3f} on {market_id} (cost: ${cost:.2f})")
        
        return self.orders[order_id]
    
    def _log_trade(self, trade: PaperTrade):
        log_line = json.dumps({
            "timestamp": trade.timestamp,
            "market_id": trade.market_id,
            "side": trade.side,
            "qty": trade.qty,
            "price": trade.price,
            "cash_after": trade.cash_after,
        })
        with open(self._log_path, "a") as f:
            f.write(log_line + "\n")
    
    def get_unrealized_pnl(self) -> float:
        """Calculate unrealized P&L on all open positions based on current prices."""
        total = 0.0
        for pos_key, pos in self.positions.items():
            if pos["status"] != "open":
                continue
            market = get_market(pos["market_id"])
            if not market or not market.outcomes:
                continue
            # Find the YES outcome price
            yes_outcome = next((o for o in market.outcomes if "YES" in o.name.upper()), None)
            if not yes_outcome:
                continue
            current_price = yes_outcome.price if pos["side"] == "YES" else (1 - yes_outcome.price)
            unrealized = (current_price - pos["avg_cost"]) * pos["shares"]
            total += unrealized
        return total
    
    def get_pnl_report(self) -> dict[str, Any]:
        account = self.get_account()
        if not account:
            return {"error": "No account"}
        
        starting_cash = 50.0
        current_cash = account["cash"]
        realized = sum(t.pnl for t in self.trades if t.pnl != 0)
        unrealized = self.get_unrealized_pnl()
        total_pnl = realized + unrealized
        
        return {
            "account_id": self.account_id,
            "starting_cash": starting_cash,
            "current_cash": round(current_cash, 4),
            "realized_pnl": round(realized, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_pnl": round(total_pnl, 4),
            "total_return_pct": round((total_pnl / starting_cash) * 100, 2) if starting_cash else 0,
            "num_trades": len(self.trades),
            "num_open_positions": len([p for p in self.positions.values() if p["status"] == "open"]),
        }
    
    def resolve_market(self, market_id: str, resolution: str) -> list[dict[str, Any]]:
        """Resolve a market — winners get paid, losers lose their stake."""
        rows = fetch_all("SELECT * FROM positions WHERE market_id = ? AND status = 'open'", (market_id,))
        settlements = []
        for row in rows:
            won = (resolution == "YES" and row["outcome_id"].endswith("-YES")) or \
                  (resolution == "NO" and row["outcome_id"].endswith("-NO"))
            if won:
                payout = row["shares"] * (1.0 - row["avg_cost"])
                execute("UPDATE accounts SET cash = cash + ? WHERE id = ?", (payout, self.account_id))
                execute("UPDATE positions SET status = 'closed' WHERE id = ?", (row["id"],))
                settle(market_id, self.account_id, row["outcome_id"], row["shares"], row["avg_cost"], won=True)
                settlements.append({"position_id": row["id"], "won": True, "payout": round(payout, 4)})
            else:
                loss = row["shares"] * row["avg_cost"]
                execute("UPDATE positions SET status = 'closed' WHERE id = ?", (row["id"],))
                settle(market_id, self.account_id, row["outcome_id"], row["shares"], row["avg_cost"], won=False)
                settlements.append({"position_id": row["id"], "won": False, "loss": round(loss, 4)})
        return settlements
    
    def close_all(self) -> list[dict[str, Any]]:
        """Close all open positions at current prices."""
        open_positions = [p for p in self.positions.values() if p["status"] == "open"]
        results = []
        for pos in open_positions:
            market = get_market(pos["market_id"])
            if not market or not market.outcomes:
                continue
            yes_outcome = next((o for o in market.outcomes if "YES" in o.name.upper()), None)
            if not yes_outcome:
                continue
            current_price = yes_outcome.price if pos["side"] == "YES" else (1 - yes_outcome.price)
            pnl = (current_price - pos["avg_cost"]) * pos["shares"]
            cash_delta = pos["shares"] * current_price if pnl > 0 else 0
            execute("UPDATE accounts SET cash = cash + ? WHERE id = ?", (cash_delta + (pos["shares"] * pos["avg_cost"] if pnl < 0 else 0), self.account_id))
            execute("UPDATE positions SET status = 'closed' WHERE account_id = ? AND market_id = ?", (self.account_id, pos["market_id"]))
            results.append({"market_id": pos["market_id"], "pnl": round(pnl, 4)})
        return results


# ---------------------------------------------------------------------------
# Automated Trading Bot
# ---------------------------------------------------------------------------
@dataclass
class TradingSignal:
    market_id: str
    question: str
    side: str
    confidence: float  # 0.0 - 1.0
    price: float
    suggested_qty: float
    reason: str

class AutomatedTradingBot:
    """Scans markets, generates signals, and executes paper trades."""
    
    def __init__(self, engine: PaperTradingEngine):
        self.engine = engine
        self.signals: list[TradingSignal] = []
        self.execution_log: list[dict[str, Any]] = []
        self._running = False
        
    def scan_markets(self) -> list[Market]:
        """Fetch live markets from Polymarket gamma API."""
        markets = list_markets(status="open")
        logger.info(f"Scanned {len(markets)} open markets")
        return markets
    
    def generate_signals(self, markets: list[Market] = None) -> list[TradingSignal]:
        """Generate trading signals based on price, volume, and simple heuristics."""
        if markets is None:
            markets = self.scan_markets()
        
        signals = []
        for market in markets:
            if not market.outcomes:
                continue
            yes_outcome = next((o for o in market.outcomes if "YES" in o.name.upper()), None)
            if not yes_outcome:
                continue
            
            price = yes_outcome.price
            vol_24h = market.volume_24h
            liquidity = market.liquidity
            
            # Simple signal logic:
            # BUY YES if price < 0.55 and volume > 50k (undervalued/high activity)
            # BUY NO if price > 0.65 and volume > 50k (overpriced)
            # Avoid markets with < 10k volume (illiquid)
            
            if vol_24h < 10000:
                continue
            
            if price < 0.55 and vol_24h > 50000 and liquidity > 50000:
                confidence = min(0.9, (0.55 - price) / 0.55 + vol_24h / 1000000)
                qty = min(100, max(10, int(self.engine.get_balance() * 0.05 / price)))
                qty = max(1, min(qty, 1000))
                cost = qty * price
                if cost <= self.engine.get_balance() * 0.10:  # Max 10% per trade
                    signals.append(TradingSignal(
                        market_id=market.id,
                        question=market.question[:80],
                        side="YES",
                        confidence=round(confidence, 3),
                        price=price,
                        suggested_qty=qty,
                        reason=f"Undervalued at ${price:.3f}, vol_24h=${vol_24h:,.0f}, liq=${liquidity:,.0f}"
                    ))
            elif price > 0.65 and vol_24h > 50000:
                confidence = min(0.9, (price - 0.65) / 0.35 + vol_24h / 1000000)
                qty = min(100, max(10, int(self.engine.get_balance() * 0.05 / (1 - price))))
                qty = max(1, min(qty, 1000))
                cost = qty * (1 - price)
                if cost <= self.engine.get_balance() * 0.10:
                    signals.append(TradingSignal(
                        market_id=market.id,
                        question=market.question[:80],
                        side="NO",
                        confidence=round(confidence, 3),
                        price=1 - price,
                        suggested_qty=qty,
                        reason=f"Overpriced at ${1-price:.3f}, vol_24h=${vol_24h:,.0f}"
                    ))
        
        self.signals = signals
        logger.info(f"Generated {len(signals)} trading signals")
        return signals
    
    def execute_signals(self, signals: list[TradingSignal] = None, max_trades: int = 5) -> list[dict[str, Any]]:
        """Execute signals as paper trades."""
        if signals is None:
            signals = self.signals
        
        executed = []
        budget = self.engine.get_balance() * 0.10  # 10% per trade max
        
        for signal in signals[:max_trades]:
            try:
                cost = signal.suggested_qty * signal.price
                if cost > budget:
                    # Scale down
                    signal.suggested_qty = max(1, int(budget / signal.price))
                    cost = signal.suggested_qty * signal.price
                
                if cost <= 0:
                    continue
                
                outcome_id = f"{signal.market_id}-{signal.side}"
                result = self.engine.place_order(
                    market_id=signal.market_id,
                    outcome_id=outcome_id,
                    side=signal.side,
                    qty=signal.suggested_qty,
                    limit_price=signal.price
                )
                executed.append({
                    "signal": signal,
                    "order": result,
                    "cost": round(cost, 4),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                self.execution_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "market_id": signal.market_id,
                    "side": signal.side,
                    "qty": signal.suggested_qty,
                    "price": signal.price,
                    "cost": round(cost, 4),
                    "status": "filled"
                })
                logger.info(f"EXECUTED: {signal.side} {signal.suggested_qty} @ ${signal.price:.3f} on {signal.market_id}")
            except Exception as e:
                logger.warning(f"Failed to execute signal on {signal.market_id}: {e}")
                self.execution_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "market_id": signal.market_id,
                    "side": signal.side,
                    "error": str(e),
                    "status": "failed"
                })
        
        return executed
    
    def run_cycle(self, max_trades: int = 5) -> dict[str, Any]:
        """One full scan → signal → execute cycle."""
        markets = self.scan_markets()
        signals = self.generate_signals(markets)
        executed = self.execute_signals(signals, max_trades=max_trades)
        report = self.engine.get_pnl_report()
        return {
            "cycle": "completed",
            "markets_scanned": len(markets),
            "signals_generated": len(signals),
            "trades_executed": len(executed),
            "pnl_report": report,
            "signals": [asdict(s) for s in signals],
            "executed": executed,
        }
    
    def run_automated_loop(self, duration_seconds: int = 60, interval_seconds: int = 30):
        """Run automated trading for a given duration."""
        self._running = True
        end_time = time.time() + duration_seconds
        cycle = 0
        logger.info(f"Starting automated trading loop for {duration_seconds}s (interval: {interval_seconds}s)")
        
        while self._running and time.time() < end_time:
            cycle += 1
            logger.info(f"=== Cycle {cycle} ===")
            try:
                result = self.run_cycle()
                logger.info(f"Cycle {cycle}: {result['trades_executed']} trades, P&L: ${result['pnl_report']['total_pnl']:.2f}")
            except Exception as e:
                logger.error(f"Cycle {cycle} error: {e}")
            
            if time.time() < end_time:
                time.sleep(interval_seconds)
        
        self._running = False
        return self.engine.get_pnl_report()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
engine = PaperTradingEngine()
bot = AutomatedTradingBot(engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Paper Trading Engine starting...")
    yield
    logger.info("Paper Trading Engine shutting down.")

app = FastAPI(title="Propolymarketer Paper Trading", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok", "account_id": engine.account_id, "balance": engine.get_balance()}

@app.get("/markets")
def get_markets(category: Optional[str] = None):
    markets = list_markets(status="open", category=category)
    return {"markets": [m.model_dump() for m in markets], "count": len(markets)}

@app.get("/markets/{market_id}")
def get_market_detail(market_id: str):
    m = get_market(market_id)
    if not m:
        raise HTTPException(404, "Market not found")
    return m.model_dump()

@app.post("/orders", response_model=OrderResponse)
def place_order(order: OrderCreate):
    try:
        result = engine.place_order(order.market_id, order.outcome_id, order.side, order.qty, order.limit_price)
        return OrderResponse(order_id=result["id"], status="filled", filled_qty=order.qty, avg_price=order.limit_price)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.get("/positions")
def get_positions():
    positions = engine.get_positions()
    # Calculate unrealized P&L per position
    for p in positions:
        market = get_market(p["market_id"])
        if market and market.outcomes:
            yes = next((o for o in market.outcomes if "YES" in o.name.upper()), None)
            if yes:
                current_price = yes.price if "YES" in p["outcome_id"] else (1 - yes.price)
                p["unrealized_pnl"] = round((current_price - p["avg_cost"]) * p["shares"], 4)
    return {"positions": positions, "count": len(positions)}

@app.get("/ledger")
def get_ledger(limit: int = 50):
    entries = engine.get_ledger()[:limit]
    return {"ledger": entries}

@app.get("/pnl")
def get_pnl():
    return engine.get_pnl_report()

@app.get("/trades")
def get_trades():
    return {"trades": [t.__dict__ for t in engine.trades], "count": len(engine.trades)}

@app.post("/scan")
def scan_markets():
    markets = bot.scan_markets()
    return {"markets": len(markets), "markets_detail": [m.id for m in markets]}

@app.post("/signals")
def generate_signals():
    signals = bot.generate_signals()
    return {"signals": len(signals), "signals_detail": [asdict(s) for s in signals]}

@app.post("/execute")
def execute_trades(max_trades: int = Query(5, ge=1, le=20)):
    executed = bot.execute_signals(max_trades=max_trades)
    return {"executed": len(executed), "results": executed}

@app.post("/cycle")
def run_cycle():
    result = bot.run_cycle()
    return result

@app.post("/automate/start")
def start_automation(duration_seconds: int = Query(3600, ge=60), interval_seconds: int = Query(30, ge=5)):
    """Start automated paper trading loop (background)."""
    import threading
    def _run():
        bot.run_automated_loop(duration_seconds=duration_seconds, interval_seconds=interval_seconds)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "running", "duration": duration_seconds, "interval": interval_seconds}

@app.get("/automate/status")
def automation_status():
    report = engine.get_pnl_report()
    report["signals"] = len(bot.signals)
    report["execution_log"] = bot.execution_log[-20:]  # Last 20 entries
    return report

@app.post("/resolve/{market_id}")
def resolve_market(market_id: str, resolution: str = Query(..., pattern="^(YES|NO)$")):
    settlements = engine.resolve_market(market_id, resolution)
    return {"market_id": market_id, "resolution": resolution, "settlements": settlements}

@app.post("/close_all")
def close_all_positions():
    results = engine.close_all()
    report = engine.get_pnl_report()
    return {"closed": results, "final_report": report}

@app.get("/log")
def get_trade_log():
    log_path = engine._log_path
    if not log_path.exists():
        return {"trades": []}
    with open(log_path) as f:
        lines = [json.loads(l) for l in f.read().strip().split("\n") if l.strip()]
    return {"trades": lines}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
