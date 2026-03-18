# MEMORY

## Project
Trading Advisor / stock_trader — Django scaffold for a read-only trading decision system.

## Working rule
Update code first, then update docs (`memory.md`, `decisions.md`, `roadmap.md`) with what is done, what we are doing, and what is left.

## Current operating model
- The app generates **signals** and **alerts**.
- You make the trade manually.
- Open positions can be tracked as **paper trades** so the app can warn when a trade starts failing.
- The architecture is intentionally compatible with later auto-trading, but no broker automation is enabled now.

## What has been done
- Completed the core scaffold, ingestion, scanning, trade plans, and disciplined Discord alerting.
- Added paper-trade lifecycle support and position-monitor alerts.
- Added queue visibility, review surfaces, next-session stock queue, and operator-cycle commands.
- Added channel-based automatic delivery baseline:
  - Discord remains the fast ping path
  - Email can be enabled as a second delivery channel
  - `send_alerts` runs across enabled channels
  - `send_test_alert` verifies the enabled channels
- Added delivery-health visibility:
  - dashboard drought warning when successful delivery has gone quiet too long
  - per-channel recent sent/failed counts and failure streaks
  - `check_alert_delivery_health` command for unattended-run trust checks
- Added delivery-health escalation:
  - `OperatorNotification` model records operator-level escalation notices
  - `escalate_delivery_health` can send an escalation through enabled channels
  - `run_scheduler` can run delivery escalation checks automatically
  - dashboard now shows recent operator escalations
- Added delivery-health recovery notifications:
  - `notify_delivery_recovery` sends a recovery notice after an open incident clears
  - `run_scheduler` can run recovery checks automatically
  - dashboard now shows the last sent escalation, the last sent recovery, and whether an incident is still open
- Added a paper-trade lifecycle layer:
  - open paper trades now track lifecycle stage, active stop, active target, trailing stop %, last/high/low price, and close reason
  - signal detail now includes in-app trade management controls
  - `sync_trade_lifecycle` updates open positions from the latest price bars
  - `run_scheduler` can sync open paper trades automatically
  - dashboard now shows lifecycle summary counts and a table for open-position management

## What we are doing
- Moving from “the app can send alerts” to “the app is usable for day-to-day manual trade management.”
- Tightening the operator workflow after entry so you can manage open trades inside the app instead of outside it.

## What is left
- Add automatic close suggestions when stop / target / reversal conditions are met.
- Add partial-exit guidance after target 1.
- Add deeper analytics tying score quality to closed paper-trade results and evaluated outcomes.
- Add SMS/text only if higher-urgency escalation is actually required.
- Add user-level delivery preferences if multiple operators are introduced.
- Add incident fingerprinting / dedupe to make operator notifications smarter than a simple cooldown.


## 2026-03-09 Pack d: Held position monitoring
- Added manual held-position tracking so Mike can enter stocks actually purchased.
- Added held-position health checks for stop breach, thesis break via live SHORT signal, target reached, and deep drawdown deterioration.
- Added holdings UI, admin support, dashboard visibility, `check_held_positions`, and scheduler wiring.
- Done: manual position entry/edit/close, health snapshots, delivery-channel alerts, docs refresh.
- Doing: making actual owned-position monitoring a first-class workflow alongside paper trades.
- Left: broker import, CSV import, partial sells, multi-lot tax lots, and richer sell-rule scoring.


## 2026-03-09 Pack e: Held-position decision layer
- Added an owned-position recommendation layer on top of held-position monitoring.
- Open holdings are now classified as `sell now`, `urgent review`, `review`, `trim / exit`, or `hold`.
- Added a dedicated holding detail page with recommendation context, recent alerts, recent signals since entry, and direct close controls.
- Added urgent ranking surfaces on the dashboard and holdings list so Mike can see what needs attention first.
- Done: decision recommendations, urgent queues, holding detail page, docs refresh.
- Doing: making the app usable for real owned-position sell decisions.
- Left: broker import, CSV import, partial exits, tax-lot support, and richer gain-protection logic.


## 2026-03-09 Pack f: Held-position CSV import
- Added CSV import for held positions with preview-before-apply behavior.
- Added symbol validation against the existing instrument universe and create/update import behavior for open positions.
- Holdings page now includes an Import CSV entry point alongside manual add and check-now actions.


## 2026-03-09 Pack g: Price-band filters
- Added min/max price filters to the Signals list.
- Added a visible price column on the Signals list so filters are explainable in-app.
- Added min/max price filters to Held Positions using current price when available.
- Done: price-band filtering on two core operator pages, docs refresh.
- Doing: making the app easier to use when Mike wants to focus on only certain stock-price ranges.
- Left: saved presets, dashboard filters, and eventual broker/account sync.


## 2026-03-09 Pack h: Operator focus filters
- Added richer signal filters for direction, timeframe, and ownership state.
- Added richer holding filters for recommendation bucket, status, and source.
- Added quick filter buttons for common operator queues and adjusted dashboard entry links to land on more usable filtered views.
- Done: focus filters, faster drill-down usability, docs refresh.
- Doing: making the app easier to work from as a daily operator console.
- Left: saved presets, dashboard-level retained filters, and broker/account reconciliation.


## 2026-03-09 Pack i: Saved filter presets
- Added saved filter presets for Signals and Held Positions.
- Mike can now save and reopen repeated operator views instead of rebuilding filters every session.
- Presets are persisted in the DB via `SavedFilterPreset` and manageable from the list pages/admin.


## 2026-03-09 Pack j: Dashboard preset widgets
- Added pin-to-dashboard support for saved signal presets and saved holding presets.
- Mike can now open his saved daily signal screens and holding queues directly from the dashboard homepage.
- Signals and Holdings preset lists now support pin/unpin actions in addition to open/delete.
- Done: dashboard preset launch surface, docs refresh.
- Doing: making the homepage more usable as the first daily control panel.
- Left: broker/account reconciliation, allocation controls, and more advanced preset analytics.


## 2026-03-09 Pack l — Partial-sell workflow
- Added `HoldingTransaction` history for owned positions.
- Added partial-sell recording from holding detail so Mike can update remaining quantity after trimming.
- Added suggested action sizing to held-position recommendations and dashboard urgent queue.


## Pack 2026-03-09m — Held Position Scale-In / Buy-Add Workflow
- Added an in-app **Record added buy** workflow for open held positions.
- The app now recalculates **quantity** and **weighted average entry price** after you add shares to an existing holding.
- Added a new holding transaction event type: **BUY_ADD**.
- Holding detail now supports the full real-world loop in one place: add shares, partial sell, or close.
- This improves usability for scaling into a position instead of treating every holding as static after the first purchase.


## Pack 2026-03-09n — Holdings performance analytics
- Added a dedicated holdings performance page.
- The app now shows realized P&L, unrealized P&L, realized win rate, top open winners/losers, and recent closed holdings.
- Dashboard now links directly into this performance view so Mike can review whether the owned-position workflow is actually producing good results.


## Pack 2026-03-09o — Holding import reconciliation
- Added reconciliation review to the holding CSV import workflow.
- The preview now shows which currently open holdings are missing from the uploaded file.
- Confirmed imports can flag those absent holdings with `missing_from_latest_import` so they appear in a dedicated review queue instead of silently drifting out of sync.
- Dashboard and holdings views now surface this reconciliation state directly.

## Pack 2026-03-09p — Reconciliation resolution workflow
- Added explicit actions for holdings flagged as missing from the latest import.
- Mike can now mark a holding as reviewed-and-still-open with a reconciliation note, or close the holding directly from the reconciliation workflow.
- Added reconciliation note/resolved-at fields so mismatch decisions are documented in-app instead of living only in memory.


## 2026-03-09 Pack q
- Added a dedicated trade analytics screen.
- Added score-bucket comparison for closed paper trades and evaluated signal outcomes.
- Added strategy/timeframe filters and minimum sample-size filtering.
- Added dashboard summary visibility for closed trade count, win rate, and best current score bucket.


## 2026-03-09 Pack r
- Added min/max score filters to Signals.
- Added quick views for high-conviction longs and 60–79 review-band signals.
- Added score-posture summary cards on the Signals screen.

- 2026-03-09 Pack s: Added in-app watchlist management with a dedicated watchlist page plus add/remove actions from Signals and Holdings.


## 2026-03-09 Pack t: Watchlist bulk import and sync
- Added a preview-before-apply bulk import flow for the active watchlist.
- Mike can now upload a CSV or paste symbols directly, then review which rows are ready, duplicated, or missing from the instrument universe.
- Added optional sync mode so the active watchlist can be matched exactly to the latest confirmed import.
- Done: bulk watchlist maintenance workflow, import preview, optional deactivation of missing active symbols, docs refresh.
- Doing: reducing friction when the scan universe changes in batches.
- Left: multi-watchlist support, watchlist presets, and external/broker-fed universe sync.


## 2026-03-09 Pack u: Multi-watchlist support
- Added named watchlists with one active watchlist per user.
- Mike can now create a new watchlist in-app and switch the active scan universe without leaving the operator workflow.
- Signals, Holdings, Dashboard, and watchlist actions now key off the active watchlist instead of a hard-coded Default list.
- Done: multi-watchlist usability, active watchlist switching, navbar visibility, docs refresh.
- Doing: making universe management practical when Mike wants different groups of symbols for different trading modes.
- Left: watchlist-level presets, rename/archive/delete controls, and external universe sync.


## 2026-03-09 Pack v: Watchlist priority and notes
- Added per-symbol watchlist priority (`High`, `Normal`, `Low`) and a short operator note on active watchlist selections.
- Added priority filtering on the Watchlist page plus a dedicated edit workflow for each symbol.
- Added dashboard visibility for the count of high-priority symbols in the current active watchlist.
- Done: priority tagging, notes, priority filters, docs refresh.
- Doing: making the watchlist a more usable first-pass operator queue instead of just a flat symbol list.
- Left: watchlist rename/archive/delete controls, deeper watchlist analytics, and external universe sync.


## 2026-03-10 Pack ag: Watchlist sector board
- Added `InstrumentSelection.sector` so watchlist names can be grouped by operator-defined sector/theme.
- Added sector-aware watchlist filtering and a sector board summary on both the Watchlist page and Dashboard.
- This keeps the scan universe usable when the list grows beyond a flat set of symbols.


## 2026-03-10 Pack aj saved state
- Added holdings sector exposure grouped by the active watchlist sector/theme tags.
- Dashboard now shows a sector exposure card so Mike can see top concentration quickly.
- Holdings navigation now includes a direct **Sector exposure** screen.


## 2026-03-10 Pack ak — Concentration guardrails
- Added `max_position_weight_pct`, `max_sector_weight_pct`, and `concentration_warning_buffer_pct` to `UserRiskProfile`.
- Allocation Controls now show position-cap posture, and sector exposure now shows over/near-cap posture plus headroom by sector/theme.
- Dashboard sector exposure card now surfaces concentration posture, not just grouping.


## 2026-03-10 Pack al — Pre-trade guardrails on Signals
- Added pre-trade guardrail evaluation for candidate signals.
- Signals now show whether the suggested trade still fits cash headroom, single-position concentration, and sector/theme concentration.
- Dashboard top opportunities now surface the same guardrail posture so risk fit is visible before Mike opens the full Signals screen.


## 2026-03-17 Pack CD — Portfolio health scoring
- Added `summarize_portfolio_health_score()` as a derived service layer that weights account risk posture, drawdown monitoring, stop guardrails, holding queue pressure, and broker reconciliation debt.
- Added a dedicated page: `Allocation Controls -> Portfolio Health Score`.
- Added visibility on the dashboard and Ops Command Center so Mike sees the weighted score before drilling into the detailed queue screens.
- No schema change was needed for this pack; scoring is derived from existing summaries and stays request-time only.


## 2026-03-17 Pack CE — Portfolio health snapshot history
- Added `PortfolioHealthSnapshot` in `apps.portfolios` with score, grade, attention/urgent counts, weakest-account summary, and JSON summary payload.
- Added `save_portfolio_health_snapshot()` and `summarize_portfolio_health_history()` service helpers.
- Added manual snapshot buttons on the Portfolio Health Score page and Ops Command Center.
- Added command: `python manage.py save_portfolio_health_snapshot --username <user>`; no scheduler wiring yet.
