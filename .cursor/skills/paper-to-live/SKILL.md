---
name: paper-to-live
description: >-
  Checklist to promote dual-market agent from paper to live Zerodha trading.
  Use when enabling live mode, going live with daytrade MIS, or verifying
  credentials, risk limits, and square-off before real orders.
---

# Paper → Live Promotion Skill

## Non-negotiables
1. `python main.py --mode health` is overall OK
2. Kill switch **not** active
3. Backtests reviewed; no strategy with drawdown > 15% promoted blindly
4. Paper daytrade / paper swing observed for at least one full session
5. User explicitly confirms live

## Checklist
- [ ] `.env` has `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, `ZERODHA_ACCESS_TOKEN` (fresh daily token)
- [ ] `config/settings.yaml` `trading.mode` still `paper` until go-live moment
- [ ] `risk:` limits sized for account (daily loss, max positions, SL/TP)
- [ ] Day trade: `square_off_time` and `no_new_entries_after` confirmed IST
- [ ] `logs/` writable; journal path exists
- [ ] Symbols liquid enough for MIS
- [ ] Contingency: how to create `logs/KILL_SWITCH` quickly

## Go-live commands
```bash
python main.py --mode health
python main.py --mode status
# Only after explicit user confirmation:
# edit settings trading.mode: live   OR pass through live-capable paths
python main.py --mode daytrade   # if settings mode=live
```

## Rollback
1. Create `logs/KILL_SWITCH` (blocks new BUYs)
2. Set `trading.mode: paper`
3. If needed, square-off: stop process and run daytrade square-off path / close MIS in Kite

## Never
- Hardcode keys
- Skip paper on a new strategy
- Raise `risk_per_trade_pct` casually for live
- Leave MIS open past `square_off_time`
