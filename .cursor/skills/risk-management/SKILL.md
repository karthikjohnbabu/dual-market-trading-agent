---
name: risk-management
description: >-
  Dual-market agent risk controls: kill switch, daily loss limit, max open
  positions, stop-loss / take-profit, trade journal, HIGH RISK drawdown >15%.
  Use when changing risk config, blocking entries, adding SL/TP, kill switch,
  or reviewing why a BUY was blocked.
---

# Risk Management Skill

## Always enforce
- Position size via `trading.risk_per_trade_pct` before any order
- Drawdown **> 15%** → flag **HIGH RISK** (backtests / promotion gate)
- Paper before live; live only with explicit confirmation
- Kill switch file blocks **new entries** (exits / square-off still allowed)

## Config (`config/settings.yaml` → `risk:`)
| Key | Meaning |
|-----|---------|
| `max_open_positions` | Cap concurrent symbols |
| `daily_loss_limit_pct` | Halt new BUYs after day loss hits this % of capital |
| `stop_loss_pct` | Force SELL if price falls this % from entry |
| `take_profit_pct` | Force SELL if price rises this % from entry |
| `kill_switch_file` | Default `logs/KILL_SWITCH` — create file to freeze entries |
| `require_paper_before_live` | Warn on live start |

## Code map
- `risk/manager.py` — `RiskManager.allow_new_entry`, SL/TP, daily PnL book
- `execution/order_manager.py` — gates BUY; journals fills
- `execution/trade_journal.py` — `logs/trades.jsonl`
- `execution/day_trader.py` — SL/TP checked every cycle before signals
- `ops/health.py` — readiness including kill switch

## Ops commands
```bash
python main.py --mode health    # readiness
python main.py --mode status    # risk snapshot + recent journal
# Freeze new entries:
#   New-Item logs/KILL_SWITCH   (Windows)  or  touch logs/KILL_SWITCH
# Resume:
#   Remove-Item logs/KILL_SWITCH
```

## When adding risk features
1. Put limits in `config/settings.yaml` under `risk:`
2. Enforce in `RiskManager` (not scattered ifs)
3. Log blocks to the trade journal
4. Add a unit test in `tests/` with no network calls
