# Trading Advisor — How to Use

## What this app is

Trading Advisor is a **decision-support tool** for buying and selling stocks and crypto. It watches a list of symbols, runs technical analysis strategies against daily price data, generates directional signals with 0–100 opportunity scores, and delivers those signals to you via Discord and/or email so you can decide whether to place a trade manually.

**The app does not place trades for you.** All buying and selling happens in your own broker (Robinhood, etc.). The app's job is to surface high-quality setups, alert you in real time, help you track what you own, and warn you when positions need attention.

---

## Does it need a start/stop button?

There are two processes to run, both started from the terminal:

| Process | Command | Purpose |
|---|---|---|
| **Web server** | `python manage.py runserver` | The browser UI — run this whenever you want to use the web app |
| **Scheduler** | `python manage.py run_scheduler --username mprovost` | The scan/alert engine — run this continuously in a separate terminal window |

The web server and scheduler are independent. The UI works whether or not the scheduler is running. The scheduler works without a browser.

**In live use you start the scheduler once and leave it running.** It is market-aware: it runs a full scan cycle every 5 minutes while the market is open (9:30–16:00 ET) and slows to one cycle per hour outside those hours. You stop it with `Ctrl+C` when you are done for the day. There is no in-app start/stop button.

---

## One-time setup

### 1. Copy and fill in your environment file

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
DJANGO_SECRET_KEY=some-long-random-string
POLYGON_API_KEY=your_polygon_key          # free tier works for daily bars
DISCORD_WEBHOOK_URL=your_webhook_url      # where alerts are sent
```

For email alerts instead of (or in addition to) Discord:

```bash
ALERT_DELIVERY_EMAIL_ENABLED=true
ALERT_EMAIL_TO=you@example.com
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=you@gmail.com
EMAIL_HOST_PASSWORD=your_app_password
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=alerts@example.com
```

### 2. Run database migrations

```bash
python manage.py migrate
```

### 3. Create your login account

```bash
python manage.py createsuperuser
```

### 4. Initialise your watchlist, strategy, and risk profile

```bash
python manage.py ensure_default_setup \
  --username mprovost \
  --account-equity 25000 \
  --risk-pct 0.0025
```

This creates a Default watchlist pre-loaded with a set of starter symbols (S&P 500 names), a default strategy configuration, and a risk profile using 0.25% of equity per trade.

To load the full S&P 500 universe:

```bash
python manage.py seed_sp500
```

To load the top 20 crypto symbols:

```bash
python manage.py seed_crypto_top20
```

### 5. Verify Discord is wired

```bash
python manage.py send_test_alert --dry-run   # preview without sending
python manage.py send_test_alert             # send a real test message
```

Check your Discord channel for the test message before going further.

### 6. Backfill historical price data

The strategies need price history to calculate moving averages and other indicators. Backfill the symbols on your watchlist:

```bash
python manage.py ingest_watchlist_prices \
  --username mprovost \
  --max-symbols 5 \
  --throttle-seconds 12
```

Increase `--max-symbols` once you have confirmed ingestion is working. The throttle prevents hitting API rate limits.

---

## Choosing an operating posture

The app ships with three preset profiles. View them:

```bash
python manage.py show_operator_policy
```

Apply one:

```bash
python manage.py apply_operator_preset conservative   # fewer alerts, higher bar
python manage.py apply_operator_preset balanced       # recommended starting point
python manage.py apply_operator_preset aggressive     # more alerts, lower bar
```

Restart the server/scheduler after applying a preset so the new `.env` values are loaded.

The most important thresholds, which you can also set manually in `.env`:

| Setting | What it controls | Default |
|---|---|---|
| `ALERT_MIN_PRICE` | Suppress alerts for symbols below this price (e.g. `5` to skip penny stocks) | _(off)_ |
| `ALERT_MAX_PRICE` | Suppress alerts for symbols above this price (e.g. `500` to stay under a cap) | _(off)_ |
| `ALERT_MIN_SCORE_EVENT` | Minimum score for a fresh trigger alert (crossover, breakout) | 80 |
| `ALERT_MIN_SCORE_STATE` | Minimum score for an ongoing condition alert | 60 |
| `ALERT_MAX_PER_DAY` | Maximum alerts sent per day | 12 |
| `ALERT_COOLDOWN_MINUTES` | Minimum time between alerts for the same symbol | 30 |

---

## Starting the scheduler (live mode)

Open a terminal and run:

```bash
python manage.py run_scheduler \
  --username mprovost \
  --watchlist Default \
  --max-symbols 25 \
  --throttle-seconds 2
```

Leave this terminal running. Every cycle the scheduler will:

1. Check that your market data provider is reachable
2. Pull the latest price bars for your watchlist symbols
3. Run strategy scans and create signals
4. Evaluate the alert queue and send eligible signals to Discord/email
5. Monitor open paper trades for stop/target/trailing-stop activity
6. Check real held positions for stop breaches and deterioration
7. Evaluate past signal outcomes where enough bars have elapsed
8. Save a portfolio health snapshot and check for deterioration (every 4th cycle)

**You do not need to do anything else to receive alerts.** The scheduler handles everything automatically.

To stop the scheduler, press `Ctrl+C` in that terminal window.

---

## Daily workflow — from alert to trade decision

### Step 1 — Receive a Discord alert

When the scheduler finds a qualifying signal it sends a message to your Discord channel containing:
- **Symbol** and **direction** (LONG = buy candidate, SHORT = sell/exit candidate)
- **Score** (0–100; higher = stronger setup)
- **Kind**: EVENT (fresh trigger) or STATE (ongoing condition still in place)
- **Trade plan**: suggested entry price, stop price, and two targets
- **Rationale**: which components contributed (trend, RSI, breakout, volume, volatility, regime)

### Step 2 — Review the signal in the UI

Open the app in your browser, go to **Signals**, and find the alert. Each signal shows:
- The full score breakdown by component
- The suggested trade plan with position size calculated from your risk profile
- A **Guardrails** column showing whether the suggested trade fits your concentration limits

Guardrail values:
- `Fits` — the trade fits within your cash headroom, position cap, and sector cap
- `Near` — it fits but leaves little room under one limit
- `Over` — the trade would push you past a concentration guardrail; review before entering

### Step 3 — Decide and act

**If you decide to take the trade**, buy it manually in your broker, then record it in the app:

1. Go to **Holdings → Add position** (or use the **Record held position** action on the signal detail page)
2. Enter the symbol, quantity, and your actual entry price
3. Optionally set your stop and target to match what you set in the broker

**If you want to simulate the trade first** (paper trading), open a paper trade from the signal detail page instead. Paper trades are tracked automatically and do not affect real money.

**If you decide to skip the signal**, mark it as Skipped in the UI so it leaves the review queue.

---

## Monitoring open positions

### Paper trades

The scheduler syncs all open paper trades every cycle. When a paper trade's price crosses the stop or hits a target, the app marks it closed and calculates P&L. You can also close a paper trade manually:

```bash
python manage.py close_paper_trade --trade-id <id> --exit-price 123.45
```

### Real held positions

The scheduler checks your held positions every cycle for:
- **Stop breach** — price has fallen through your stop
- **Target reached** — price has hit your first or second target
- **Thesis break** — a live SHORT signal appeared for a stock you are long (or vice versa)
- **Deep deterioration** — unrealized loss has exceeded the configured threshold

When any of these fire, the app sends an alert to Discord/email and flags the position in the **Holdings** dashboard.

**In the Holdings UI you can:**
- See each position's current recommendation: `Sell now`, `Urgent review`, `Review`, `Trim / exit`, or `Hold`
- Record a partial sale (reduces quantity, logs the realized P&L)
- Record an add-to-position buy (recalculates average entry price)
- Close the position fully

---

## Reading signals and scores

Scores run from 0 to 100. The strategies that generate them are:

**`trading_brain`** (primary) — a multi-factor composite:
- Trend alignment (MA5 vs MA10 direction and separation)
- Momentum (rate of MA change)
- RSI confirmation (overbought/oversold context)
- Breakout confirmation (price vs recent range high/low)
- Volume spike (current vs average volume)
- Volatility quality (ATR as % of price)
- Market regime (TRENDING / SIDEWAYS / VOLATILE — adjusts the weight mix automatically)

**`ma_crossover`** (secondary) — simpler: fires when the fast MA crosses the slow MA with ATR-based stop/target sizing.

A score of **80+** on an EVENT signal is the strongest setup the system can identify. A score of **60–79** is a reasonable setup worth reviewing. Below 60 the signal is filtered out by default.

---

## Watchlist management

The watchlist determines which symbols get scanned each cycle. Open **Watchlist** in the nav to:
- Add or remove symbols
- Set priority (High / Normal / Low) to focus the daily universe
- Tag symbols with a sector or theme (Semis, AI, Dividend, etc.) for concentration tracking
- Create multiple named watchlists and switch between them
- Bulk-import symbols from a CSV

To make a watchlist active for scanning, click **Use** next to it on the Watchlist page.

---

## Portfolio health score

Open **Allocation Controls → Portfolio Health Score** for a single triage number before drilling into details.

The score starts at 100 for each tracked account and loses points for:
- Risk posture violations (over/near concentration caps)
- Drawdown stress
- Stop-guardrail debt (open positions without stops)
- Sell/review queue pressure
- Broker reconciliation drift

Grades: **Stable (85–100) → Working (70–84) → Watch (50–69) → Action needed (35–49) → Critical (<35)**

The scheduler automatically saves a snapshot every 4 cycles and sends you a Discord/email alert if the score drops 10+ points between snapshots. You can also save a manual snapshot anytime:

```bash
python manage.py save_portfolio_health_snapshot --username <your_username>
```

---

## Broker reconciliation

If your broker lets you export a CSV of open positions:
1. Go to **Holdings → Import CSV**
2. Upload the file (needs at minimum `symbol`, `quantity`, `average_entry_price` columns)
3. The app compares the import against what it has on record and flags any holdings missing from the file
4. Work through the **Missing from latest import** queue to either mark them reconciled or close them

---

## Ops Command Center

Open **Allocation Controls → Ops Command Center** for a single-page view of everything that needs attention:
- Delivery trust (have alerts been sending successfully?)
- Stop-policy queue
- Evidence lifecycle queue
- Broker snapshot drift
- Recent broker reconciliation runs
- Portfolio health score

---

## Analytics

Open **Analytics** in the nav to review historical performance:
- Filter by timeframe, strategy, and minimum sample size
- Compare closed paper trades by score bucket to see if higher scores actually perform better
- Compare signal outcomes by strategy and timeframe
- Identify whether your score thresholds are calibrated correctly

---

## Useful commands for manual operation

If you prefer to drive the app manually instead of leaving the scheduler running:

```bash
# Run one complete scan + alert + monitoring cycle
python manage.py run_operator_cycle --username <you> --max-symbols 25

# Dry-run the same cycle without sending alerts
python manage.py run_operator_cycle --username <you> --dry-run --verbose-scan

# See what signals are in the queue right now and why each is eligible or blocked
python manage.py preview_alert_queue --username <you> --limit 20

# See what would go out at the next session open
python manage.py preview_next_session_queue --username <you> --limit 10

# Manually check delivery health
python manage.py check_alert_delivery_health

# View system health overview
python manage.py system_health --username <you>

# Print this guide
python manage.py show_operator_guide
```

---

## Troubleshooting a quiet system

If you are not receiving alerts:

1. **Check the queue**: `python manage.py preview_alert_queue --username <you>`
   - If signals are listed as BLOCKED, the reason column tells you why (score too low, cooldown, outside session hours, already sent recently, signal too old)

2. **Lower the score threshold temporarily** to confirm delivery is working end to end:
   ```bash
   ALERT_MIN_SCORE_EVENT=30
   ALERT_MIN_SCORE_STATE=40
   ```
   Restart the scheduler, wait one cycle, then restore the thresholds once you have confirmed a message arrived in Discord.

3. **Check delivery health**: `python manage.py check_alert_delivery_health`

4. **Verify data ingestion**: `python manage.py provider_healthcheck --providers polygon,coinbase`

5. **Check that there is price data**: `python manage.py system_health --username <you>`
   - If the instrument count is 0 or price bar count is 0, ingestion has not run yet — re-run `ingest_watchlist_prices`

6. **Check market session**: Alerts are only sent between 09:30 and 16:00 ET by default. Use `preview_next_session_queue` to see what is queued for the next session open.

---

## Key settings reference

```bash
# Market data
POLYGON_API_KEY=                         # required for stock data
STOCK_DAILY_PROVIDER=polygon             # polygon or yahoo

# Alerts
DISCORD_WEBHOOK_URL=
ALERT_MIN_PRICE=                         # e.g. 5 — skip signals below this price
ALERT_MAX_PRICE=                         # e.g. 500 — skip signals above this price
ALERT_MIN_SCORE_EVENT=80                 # 0–100, raise to reduce noise
ALERT_MIN_SCORE_STATE=60
ALERT_MAX_PER_DAY=12
ALERT_COOLDOWN_MINUTES=30
ALERT_MAX_SIGNAL_AGE_MINUTES=4320        # 3 days; older signals are suppressed
EQUITY_ALERT_SESSION_START=09:30
EQUITY_ALERT_SESSION_END=16:00

# Scheduler pacing
SCHEDULER_INTERVAL_SECONDS=300          # 5 min base cycle
SCHEDULER_OPEN_SLEEP_SECONDS=300        # 5 min during market hours
SCHEDULER_CLOSED_SLEEP_SECONDS=3600     # 60 min outside market hours
SCHEDULER_MAX_SYMBOLS_PER_CYCLE=25      # increase for larger watchlists
SCHEDULER_THROTTLE_SECONDS=2            # pause between provider calls

# Position monitoring
HELD_POSITION_DETERIORATION_ALERT_PCT=5.0   # alert when unrealized loss hits 5%
HELD_POSITION_REVIEW_WARNING_PCT=2.5        # softer review warning at 2.5%
HELD_POSITION_SELL_ON_SHORT_WITH_LOSS=true  # auto-flag a long when a short signal fires

# Portfolio health
PORTFOLIO_HEALTH_DETERIORATION_THRESHOLD=10    # alert when score drops 10+ points
PORTFOLIO_HEALTH_ALERT_COOLDOWN_MINUTES=120
SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY=4           # save snapshot every 4 cycles
```
