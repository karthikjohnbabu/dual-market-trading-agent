"""Tests for RiskManager gates (no network)."""

from __future__ import annotations

from pathlib import Path

from risk.manager import RiskManager


def _config(tmp_path: Path | None = None) -> dict:
    kill = "logs/KILL_SWITCH_TEST"
    if tmp_path is not None:
        kill = str(tmp_path / "KILL_SWITCH")
    return {
        "trading": {"capital_inr": 100_000, "risk_per_trade_pct": 1, "mode": "paper"},
        "risk": {
            "max_open_positions": 2,
            "daily_loss_limit_pct": 2,
            "stop_loss_pct": 1.0,
            "take_profit_pct": 2.0,
            "kill_switch_file": kill,
        },
    }


def test_max_open_positions_blocks_third_entry() -> None:
    rm = RiskManager(_config())
    assert rm.allow_new_entry("AAA")["allowed"] is True
    rm.register_entry("AAA", 10, 100.0)
    assert rm.allow_new_entry("BBB")["allowed"] is True
    rm.register_entry("BBB", 10, 100.0)
    blocked = rm.allow_new_entry("CCC")
    assert blocked["allowed"] is False
    assert "max_open_positions" in blocked["reason"]


def test_stop_loss_and_take_profit() -> None:
    rm = RiskManager(_config())
    rm.register_entry("RELIANCE", 10, 1000.0)
    assert rm.check_stop_take_profit("RELIANCE", 995.0) is None
    assert rm.check_stop_take_profit("RELIANCE", 989.0) == "STOP_LOSS"
    assert rm.check_stop_take_profit("RELIANCE", 1025.0) == "TAKE_PROFIT"


def test_daily_loss_halt(tmp_path: Path) -> None:
    rm = RiskManager(_config(tmp_path))
    rm.register_entry("INFY", 100, 100.0)
    # Realize -2500 on 100k capital = -2.5% > 2% limit
    pnl = rm.register_exit("INFY", 75.0)
    assert pnl == -2500.0
    blocked = rm.allow_new_entry("TCS")
    assert blocked["allowed"] is False
    assert "daily_loss" in blocked["reason"]


def test_kill_switch_file(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    rm = RiskManager(cfg)
    assert rm.allow_new_entry("TCS")["allowed"] is True
    path = Path(cfg["risk"]["kill_switch_file"])
    # RiskManager resolves relative to project root — use absolute via monkeypatch path
    # Write under project logs using the relative name inside tmp won't work.
    # Instead create file where manager looks: project_root / kill path.
    from risk import manager as risk_mod

    target = risk_mod.PROJECT_ROOT / cfg["risk"]["kill_switch_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stop", encoding="utf-8")
    try:
        blocked = rm.allow_new_entry("TCS")
        assert blocked["allowed"] is False
        assert "kill_switch" in blocked["reason"]
    finally:
        if target.exists():
            target.unlink()
