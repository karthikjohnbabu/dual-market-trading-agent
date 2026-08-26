---
name: trading-strategies
description: >-
  Dual-market trading agent strategies catalog (momentum swing, intraday
  day-trading MIS with same-day square-off), how to add strategies/brokers,
  and risk guardrails. Use when implementing or changing strategies, day
  trading, square-off, MIS/CNC, backtests, or when Prasath/contributors ask
  about trading rules.
---

# Trading Strategies Skill

## When to use
- Adding or changing a strategy under `strategies/`
- Day trading / MIS / same-day square-off work
- Explaining what the agent trades (India Zerodha vs UK eToro)
- Answering Prasath Anna or contributors about auto buy/sell behavior

## Product rules (always enforce)
- Never hardcode API keys — `.env` or `config/settings.yaml` only
- Do not modify `execution/order_manager.py` without explicit user confirmation
- Validate position size before any order (`risk_per_trade_pct`)
- Flag drawdown **> 15%** as **HIGH RISK**
- Paper trade first; live only after explicit confirmation
- Keep files focused (< ~300 lines), type hints + docstrings

## Strategy catalog

### 1. Momentum swing (`strategies/momentum.py`)
- **Style:** multi-day / swing (CNC by default in order path)
- **Signals:** `indicators/signals.py` → EMA fast/slow, RSI, volume confirmation
- **BUY:** `ema_fast > ema_slow` AND `rsi < rsi_overbought` AND `volume_signal`
- **SELL:** `ema_fast < ema_slow` OR `rsi > rsi_overbought`
- **HOLD:** otherwise
- **Config:** `config/settings.yaml` → `momentum:` + `india.timeframe: day`
- **Run:** `python main.py --mode backtest|paper|live|scan`

### 2. Day trading MIS (`strategies/day_trading.py` + `execution/day_trader.py`)
- **Requirement (Prasath Anna):** day trade, auto buy/sell, **square off same day**
- **Product:** `MIS` (intraday), not overnight CNC
- **Bars:** `day_trading.timeframe` (default `5minute`)
- **Session (IST):** open `09:15`, no new BUY after `14:45`, square-off `15:15`, close `15:30`
- **Square-off:** at/after `square_off_time`, close all MIS/paper positions; no overnight
- **Signals:** same momentum rules on intraday candles
- **Config:** `day_trading:` block in `settings.yaml`
- **Run:** `python main.py --mode daytrade` (paper unless config/CLI is live)

### 3. Mean reversion (`strategies/mean_reversion.py`)
- **Style:** fade extremes (RSI + close z-score)
- **BUY:** RSI < oversold AND z-score <= `-entry_z`
- **SELL:** RSI > overbought OR z-score >= `entry_z`
- **Config:** `mean_reversion:` in `settings.yaml`
- Wire into runners when selecting strategy (momentum remains default for daytrade/scan)

### 4. Planned / not yet coded
| ID | Idea | Status |
|----|------|--------|
| breakout | High-volume range break | planned |
| pairs | India pair relative value | planned |
| uk_momentum | Local signals for eToro watchlist | partial (scan/backtest only; eToro read-only) |

## Risk + ops (see also skills `risk-management`, `paper-to-live`)
- `risk:` max positions, daily loss halt, SL/TP, kill switch file
- Journal: `logs/trades.jsonl`
- `python main.py --mode health|status`

## How to add a new strategy
1. Create `strategies/<name>.py` with a class; reuse `indicators/` helpers
2. Wire into `backtest/engine.py` and/or `execution/` / `main.py` modes
3. Add params under `config/settings.yaml`
4. Add tests in `tests/` (no real API calls — mock brokers)
5. Document the strategy in **this skill** (catalog table + rules)
6. Backtest → paper → live; flag HIGH RISK if max DD > 15%

## How to add a new broker
1. Adapter in `brokers/<name>.py` matching ZerodhaClient surface where possible
2. Credentials via `.env` + `.env.example` docs
3. Paper mode must no-op real orders
4. Register in execution path carefully (order_manager confirmation required)

## CLI cheat sheet
```bash
python main.py --mode backtest   # historical summary table
python main.py --mode scan       # BUY/SELL/HOLD snapshot
python main.py --mode paper      # swing loop (CNC default)
python main.py --mode daytrade   # MIS intraday + 15:15 square-off
python main.py --mode health     # readiness / kill switch / creds
python main.py --mode status     # risk book + recent journal
python main.py --mode live       # WARNING: real orders if credentials set
pytest tests/
```

## Reply template for Prasath (day trading)
When asked if the system day-trades and squares off same day:
- **Yes for `--mode daytrade`:** MIS product, auto signals, forced square-off at `day_trading.square_off_time` (default 15:15 IST).
- **Not for default swing `paper`/`live`:** those use daily bars / CNC-style path unless product overridden.
