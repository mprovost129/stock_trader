# Trading Advisor / stock_trader

Django-based manual-execution trading advisor. The app ingests market data, scans for rule-based signals, builds trade plans, and sends disciplined operator alerts.

## Latest update — Portfolio health snapshots
- Added persistent **Portfolio Health Snapshot** records so score changes are visible over time instead of relying on memory.
- Added a **Save snapshot** action on the Portfolio Health Score page and inside the Ops Command Center.
- Added a management command: `python manage.py save_portfolio_health_snapshot --username <user>` for repeatable checkpointing.


## Automatic alerts
The current automatic delivery baseline supports:
- Discord webhooks
- Email via Django email backend

Delivery is channel-based and controlled from environment settings. The dashboard shows enabled channels, recent failures, delivery-health posture, and the latest operator notices.

## Automatic escalation and recovery
When delivery health degrades, the system can raise an operator escalation notice through the same enabled channels. When delivery health returns to normal after an open incident, the system can now send a recovery notice so unattended runs are trustworthy end-to-end.

Current intent:
- Discord = fast operator ping
- Email = fallback / audit trail
- SMS/Text = not implemented in this codebase yet


## Saved filter presets
Signals and Held Positions now support saved filter presets. Mike can save a filter combination like `longs not held under $25` or `sell now open holdings`, reopen it in one click, and keep using the app as an operator console instead of rebuilding the same filters every session.

## Paper-trade lifecycle layer
Open paper trades now have a real lifecycle surface instead of a simple open/closed state.
The app tracks:
- lifecycle stage
- active stop
- active target
- optional trailing-stop percentage
- highest/lowest/last seen price
- closed reason

That gives you a usable operator loop for manually managing a trade after the initial signal fires.


## 2026-03-09 Pack d: Held position monitoring
- Added manual held-position tracking so Mike can enter stocks actually purchased.
- Added held-position health checks for stop breach, thesis break via live SHORT signal, target reached, and deep drawdown deterioration.
- Added holdings UI, admin support, dashboard visibility, `check_held_positions`, and scheduler wiring.
- Done: manual position entry/edit/close, health snapshots, delivery-channel alerts, docs refresh.
- Doing: making actual owned-position monitoring a first-class workflow alongside paper trades.
- Left: broker import, CSV import, partial sells, multi-lot tax lots, and richer sell-rule scoring.


## 2026-03-09 Pack e: Held-position decision layer
- Added an explicit sell/review recommendation layer for held positions.
- Open owned positions are now classified in-app as `sell now`, `urgent review`, `review`, `trim / exit`, or `hold`.
- Added a dedicated holding detail page with recent alerts, recent signals since entry, and a direct close workflow.
- Added dashboard + holdings-page urgent queues so Mike can see what needs attention first without scanning every row manually.
- Done: recommendation logic, detail page, urgent queue, docs refresh.
- Doing: turning held-position monitoring into a real operator decision workflow instead of just alert generation.
- Left: broker import, CSV import, partial exits, multi-lot tax lots, and more nuanced profit-protection rules.


## Latest update — Held-position CSV import
- Bulk CSV import now lets you load owned stocks without manual one-by-one entry.
- The import flow validates symbols against the instrument universe, previews every row, then creates or updates open holdings after confirmation.


## 2026-03-09 Pack g: Price-band filters
- Added min/max price filters to the Signals list so Mike can focus on cheaper or higher-priced names instead of scanning the full feed.
- Added min/max price filters to Held Positions so owned stocks can be narrowed by current price band.
- Done: price-band filtering on signals and holdings, price column on signals list, docs refresh.
- Doing: tightening day-to-day usability so Mike can work the app by price segment, not just by symbol or recommendation.
- Left: dashboard-level price widgets, saved filter presets, and broker/account sync.


## 2026-03-09 Pack h: Operator focus filters
- Added richer signal filters for direction, timeframe, and ownership state so Mike can separate stocks he already owns from fresh candidates.
- Added richer holding filters for recommendation bucket, open/closed state, and source (manual vs import).
- Added quick filter buttons on Signals and Held Positions for the most common day-to-day operator views.
- Done: operator focus filters, dashboard quick-link posture, docs refresh.
- Doing: making the app feel more like a usable screener and review console instead of a raw list of records.
- Left: saved filter presets, dashboard-level widgets, and broker/account reconciliation.


## 2026-03-09 Pack i: Saved filter presets
- Added persistent saved filter presets for both Signals and Held Positions.
- Added save-current-filter forms and one-click open/delete actions on both list pages.
- Added `SavedFilterPreset` admin support so presets are inspectable in Django admin.
- Done: reusable operator screeners and queues, preset persistence, docs refresh.
- Doing: reducing repeated filter setup work so Mike can stay focused on decisions.
- Left: dashboard preset shortcuts, broker reconciliation, and allocation controls.


## Latest update — Partial-sell workflow
- Owned holdings now support direct partial sells from the holding detail page.
- The app records lightweight transaction history for manual opens, import syncs, partial sells, and full closes.
- Sell/review recommendations now include a suggested action size so Mike can see how much to cut when the app says `trim / exit`, `review`, or `sell now`.


## Pack 2026-03-09m — Held Position Scale-In / Buy-Add Workflow
- Added an in-app **Record added buy** workflow for open held positions.
- The app now recalculates **quantity** and **weighted average entry price** after you add shares to an existing holding.
- Added a new holding transaction event type: **BUY_ADD**.
- Holding detail now supports the full real-world loop in one place: add shares, partial sell, or close.
- This improves usability for scaling into a position instead of treating every holding as static after the first purchase.


## New in Pack 2026-03-09n
- Holdings performance review page with realized vs unrealized P&L
- Top unrealized winners/losers for open positions
- Realized performance by position and recent closed holdings


## Latest usability update
- Import reconciliation review now compares an uploaded holdings CSV against currently open positions and flags anything missing from the latest import for manual review.

## Latest update — Reconciliation resolution workflow
Holdings that are missing from the latest account CSV import are no longer just flagged. They now have a real operator workflow: keep the holding open with a reconciliation note, or close it directly from reconciliation review.


## Latest update — Trade analytics
- Added a dedicated **Trade analytics** page.
- It compares closed paper-trade results and evaluated signal outcomes by score bucket so Mike can judge whether high-score setups are actually performing better.
- It also breaks down closed paper trades by strategy and timeframe, with filter controls for timeframe, strategy, and minimum sample size.


## Latest update — Score-band signal filtering
The Signals screen now supports `min_score` and `max_score` filters. Mike can jump straight into high-conviction setups like `score >= 80`, or keep review work in a narrower band like `60–79`, without scanning the full feed.


## Latest usability pack
- Added in-app watchlist management so the active scan universe can be updated from Signals, Holdings, or the Watchlist page.


## Latest update — Watchlist bulk import
- The active watchlist now supports preview-before-apply bulk import.
- Mike can upload a CSV with a `symbol`/`ticker` column or paste a raw symbol list.
- The import preview shows which symbols are ready, duplicated, or missing from the instrument universe before anything changes.
- Optional sync mode can deactivate currently active symbols that are not present in the latest import.

- Multi-watchlist support is now available in-app: Mike can create named watchlists, switch the active scan universe, and see the active watchlist in the navbar.


## Latest update — Watchlist priority and notes
- Active watchlist symbols can now carry a priority (`High`, `Normal`, `Low`) plus a short operator note.
- Mike can filter the active watchlist by priority and edit symbol-specific watchlist context without removing and re-adding the name.
- The dashboard now surfaces the count of high-priority names in the active watchlist so first-pass review starts with the symbols that matter most.


## Latest update — Watchlist sector board
- Active watchlist symbols now support an optional sector/theme tag.
- The watchlist page can filter by sector/theme and now shows a sector board summary with counts, posture, and leaders so Mike can see where strength is clustering.
- The dashboard now surfaces the same sector board for the active watchlist.


## Holdings sector exposure
Owned holdings can now be grouped by the active watchlist's sector/theme tags. This gives Mike a practical concentration view without needing an external portfolio spreadsheet. Use **Held Positions → Sector exposure** to review market value, portfolio weight, unrealized P&L, and the largest symbols inside each bucket.


## 2026-03-10 Pack ak — Concentration guardrails
- Added soft concentration limits to Allocation Controls for single positions and sector/theme exposure.
- Sector exposure screens now classify buckets as `OK`, `Near`, or `Over` so Mike can see crowding risk quickly.
- Dashboard and allocation pages now surface concentration posture alongside raw market value.


## Pre-trade guardrails
- The Signals screen now evaluates each suggested trade against three operator guardrails before entry: remaining cash headroom, projected single-position weight, and projected sector/theme weight.
- Projected weights are evaluated against account equity from Allocation Controls so Mike can tell whether a trade still fits before buying it.
- The dashboard Top scored opportunities table now shows the same guardrail posture (`Fits`, `Near`, `Over`, or missing profile/plan) for the current top ideas.


## Latest operator surface
- **Portfolio Health Score** now sits beside Allocation Controls and Ops. Use it as the first triage screen when you want one weighted answer to: *which account needs attention first?*
- The scorecard does not replace the detailed queue pages. It points you back to holdings / reconciliation / stop work after the weakest account has been identified.
