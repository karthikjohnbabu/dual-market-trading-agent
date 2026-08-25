# Dual-Market Trading Agent

A dual-market algorithmic trading system for **India (NSE/BSE via Zerodha Kite Connect)** and the **UK (eToro portfolio monitoring)**. The agent builds momentum strategies, runs backtests, and supports paper trading before any live execution.

> **Safety first:** Always paper trade before live. Never commit API keys. Strategies with drawdown above 15% are flagged as **HIGH RISK**.

---

## 1. Project Overview

This project is a Python-based trading agent that:

| Market | Broker | Role |
|--------|--------|------|
| India (NSE/BSE equities) | Zerodha Kite Connect | Data, signals, and order execution |
| UK | eToro | Portfolio monitoring (read-only); signals generated locally |

**What you can do with it:**

- Build and refine momentum-based strategies
- Fetch market data and compute indicators
- Backtest strategies with risk metrics (position sizing, stop-losses, drawdown)
- Paper trade safely, then go live only after explicit confirmation

**Stack:** Python 3.11+, pandas, numpy, TA-Lib, kiteconnect, vectorbt, python-dotenv

---

## 2. Repo Structure

```text
dual-market-trading-agent/
├── brokers/          # Broker adapters (Zerodha, eToro)
├── strategies/       # Trading strategy logic (e.g. momentum)
├── indicators/       # Technical indicators and signal helpers
├── data/             # Market data fetching and caching
├── backtest/         # Backtesting engine and reports
├── execution/        # Order management and position sizing
├── config/           # settings.yaml and non-secret config
├── mcp_servers/      # MCP server wrappers for broker tools
├── tests/            # Unit and strategy tests
├── logs/             # Runtime logs (*.log files are gitignored)
├── .env.example      # Template for API keys and secrets
├── requirements.txt  # Python dependencies
└── README.md         # You are here
```

| Folder | Purpose |
|--------|---------|
| `brokers/` | Connect to exchanges/brokers; keep API-specific code here |
| `strategies/` | Define entry/exit rules; one strategy per module when possible |
| `indicators/` | Shared indicator and signal utilities |
| `data/` | Download and prepare OHLCV / market data |
| `backtest/` | Run historical simulations and risk metrics |
| `execution/` | Validate size, place/cancel orders (`order_manager.py`) |
| `config/` | YAML settings (no secrets) |
| `mcp_servers/` | Optional MCP integrations for agent tooling |
| `tests/` | Pytest coverage for strategies and helpers |
| `logs/` | Local log output |

---

## 3. Setup Instructions

### Prerequisites

- **Python 3.11+**
- Git
- A Zerodha account (for India trading) and/or an eToro account (for UK monitoring)

### Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/<your-org>/dual-market-trading-agent.git
   cd dual-market-trading-agent
   ```

2. **Create and activate a virtual environment**

   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # macOS / Linux
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   > **Note:** TA-Lib may need a system library install first. On Windows, use a pre-built wheel; on macOS, `brew install ta-lib`; on Linux, install `ta-lib` via your package manager.

4. **Configure environment variables**

   ```bash
   # Windows
   copy .env.example .env

   # macOS / Linux
   cp .env.example .env
   ```

   Open `.env` and fill in your keys (never commit this file):

   ```env
   ZERODHA_API_KEY=your_zerodha_api_key
   ZERODHA_API_SECRET=your_zerodha_api_secret
   ZERODHA_ACCESS_TOKEN=your_access_token
   ETORO_API_KEY=your_etoro_api_key
   ```

5. **Review non-secret settings**

   Edit `config/settings.yaml` for symbols, risk limits, and mode (`paper` / `live`).

---

## 4. Broker Setup

### Zerodha Kite Connect (India — NSE/BSE)

1. Create / log in to a [Zerodha](https://zerodha.com/) account.
2. Open the [Kite Connect developer portal](https://developers.kite.trade/).
3. Create an app to get your **API key** and **API secret**.
4. Complete the login flow to generate a daily **access token** (Kite tokens expire; refresh as needed).
5. Put `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, and `ZERODHA_ACCESS_TOKEN` in `.env`.

Useful links:

- [Kite Connect docs](https://kite.trade/docs/connect/v3/)
- [Python client (`kiteconnect`)](https://github.com/zerodha/pykiteconnect)

### eToro (UK — read-only monitoring)

1. Log in to your [eToro](https://www.etoro.com/) account.
2. Request / create API credentials from eToro’s developer or partner API program (availability depends on your account and region).
3. Put `ETORO_API_KEY` (and any related secrets from `.env.example`) in `.env`.
4. In this project, eToro is used for **portfolio monitoring (read-only)**. Trading signals for UK names are generated locally; do not assume live eToro order placement unless the code explicitly supports it and you have confirmed it.

> **Never hardcode API keys** in Python files. Use `.env` or `config/settings.yaml` (settings for non-secrets only).

---

## 5. How to Run

Follow this order every time: **backtest → paper → live**.

### Step 1 — Backtest

Run the backtest engine against historical data and review metrics (returns, max drawdown, win rate).

```bash
python -m backtest.engine
```

- If **max drawdown > 15%**, treat the strategy as **HIGH RISK** and do not promote it to live without a clear risk review.
- Tune parameters in `config/settings.yaml` and re-run until results are acceptable.

### Step 2 — Paper trade

Enable paper / simulation mode in config (or via env), then run the agent without sending real orders.

```bash
# Example — exact flags may match your settings.yaml
python -m execution.order_manager --mode paper
```

Confirm fills, position sizing, and stop-loss behavior look correct in the logs under `logs/`.

### Step 3 — Live trade (only after confirmation)

Live trading requires **explicit user confirmation**. Do not switch to live by default.

1. Confirm paper results and risk limits.
2. Set mode to `live` in `config/settings.yaml` (or the documented CLI flag).
3. Start the agent and monitor logs closely.

```bash
python -m execution.order_manager --mode live
```

**Guardrails enforced by this project:**

- Position size is validated before any order
- `execution/order_manager.py` must not be changed without explicit confirmation from the owner
- Live mode only after you intentionally enable it

---

## 6. For Contributors (Prasath Anna section)

Welcome — this section is for contributors picking up the repo fresh (including **Prasath Anna**).

### How to add a new strategy

1. Create a new module under `strategies/`, e.g. `strategies/mean_reversion.py`.
2. Define a clear class or functions with:
   - **Type hints** on all functions
   - **Docstrings** on every class and public method
3. Reuse helpers from `indicators/` instead of duplicating indicator math.
4. Register or import the strategy where the backtest / runner expects it (see `strategies/__init__.py` and `backtest/engine.py`).
5. Add tests in `tests/` (copy patterns from `tests/test_strategy.py`).
6. Backtest first; flag **HIGH RISK** if drawdown exceeds 15%.

Keep files focused: prefer small functions, and keep each file under **~300 lines**.

### How to add a new broker

1. Add a new adapter under `brokers/`, e.g. `brokers/interactive_brokers.py`.
2. Mirror the public interface used by `brokers/zerodha.py` / `brokers/etoro.py` (auth, fetch data, place/cancel if applicable).
3. Load credentials from `.env` via `python-dotenv` — never hardcode keys.
4. Wire the adapter into `execution/` only through the shared order/validation path.
5. Document any new env vars in `.env.example`.
6. Add or extend tests so paper mode works before any live path.

### Contribution habits

- Prefer **small diffs** over full-file rewrites for minor changes
- Do not modify `execution/order_manager.py` unless the owner explicitly asks
- Run tests before opening a PR:

  ```bash
  pytest
  ```

---

## 7. Disclaimer

This repository is for **educational and research purposes only**. It is **not financial advice**.

- Trading equities and CFDs involves substantial risk of loss.
- Past backtest performance does not guarantee future results.
- You are solely responsible for any capital you deploy.
- The authors and contributors are not liable for trading losses, API misuse, or broker account issues.
- Always verify broker terms, local regulations, and tax obligations in your jurisdiction (India, UK, or elsewhere).

**Paper trade first. Live trade only after you understand the risks and explicitly confirm.**

---

## License

Add a license file if/when you open-source this project. Until then, treat the code as private to the repository owners unless otherwise stated.
