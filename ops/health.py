"""Ops health checks for env, config, risk, and filesystem readiness."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from data.fetch import load_config
from risk.manager import RiskManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_health_check() -> dict[str, Any]:
    """Return a structured health report (no broker network calls required)."""
    load_dotenv()
    config = load_config()
    risk = RiskManager(config)

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("config_loaded", True, "config/settings.yaml readable")
    add(
        "trading_mode",
        str(config.get("trading", {}).get("mode", "")).lower() in {"paper", "live"},
        f"mode={config.get('trading', {}).get('mode')}",
    )

    logs_dir = PROJECT_ROOT / "logs"
    add("logs_dir", logs_dir.exists(), str(logs_dir))

    z_key = bool(os.getenv("ZERODHA_API_KEY"))
    z_tok = bool(os.getenv("ZERODHA_ACCESS_TOKEN"))
    mode = str(config.get("trading", {}).get("mode", "paper")).lower()
    if mode == "live":
        add("zerodha_creds", z_key and z_tok, "required for live")
    else:
        add(
            "zerodha_creds",
            True,
            "optional in paper" if not (z_key and z_tok) else "present",
        )

    av = bool(os.getenv("ALPHA_VANTAGE_API_KEY"))
    add("alpha_vantage", True, "present" if av else "missing (UK fetch will fail)")

    kill = risk.kill_switch_active()
    add("kill_switch", not kill, "ACTIVE — new entries blocked" if kill else "clear")

    risk_status = risk.status()
    add(
        "risk_limits",
        True,
        (
            f"max_open={risk_status['max_open_positions']} "
            f"daily_loss_limit={risk_status['daily_loss_limit_pct']}% "
            f"sl={risk_status['stop_loss_pct']}% tp={risk_status['take_profit_pct']}%"
        ),
    )

    day = config.get("day_trading", {})
    add(
        "day_trading",
        bool(day.get("enabled", False)),
        (
            f"product={day.get('product')} square_off={day.get('square_off_time')} "
            f"tf={day.get('timeframe')}"
        ),
    )

    overall = all(c["ok"] for c in checks if c["name"] != "alpha_vantage")
    return {
        "overall_ok": overall,
        "checks": checks,
        "risk": risk_status,
        "symbols_india": list(config.get("india", {}).get("symbols", [])),
        "symbols_uk": list(config.get("uk", {}).get("symbols", [])),
    }


def format_health_report(report: dict[str, Any]) -> str:
    """Pretty-print a health report for the console."""
    lines = [
        f"Overall: {'OK' if report.get('overall_ok') else 'ISSUES'}",
        "-" * 48,
    ]
    for check in report.get("checks", []):
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"[{mark}] {check['name']}: {check['detail']}")
    lines.append("-" * 48)
    lines.append(f"India symbols: {', '.join(report.get('symbols_india', []))}")
    lines.append(f"UK symbols: {', '.join(report.get('symbols_uk', []))}")
    return "\n".join(lines)
