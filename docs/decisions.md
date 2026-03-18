# DECISIONS

## Core product rule
This app is **manual-execution first**. It may later support auto-trading, but today it is an advisory and monitoring tool.

## Alerting philosophy
Automatic alerts are expected to reach the operator without manual polling. Delivery should therefore be explicit and inspectable, not implied.

## Delivery baseline
The intended delivery system is now:
- **Discord** for fast operator pings
- **Email** for inbox/audit fallback
- **SMS/Text** later only if the extra cost and dependency are justified

## Why delivery health now
The trust problem is not just “can the app send an alert?” but “would I notice quickly if automatic delivery silently stopped?” That requires drought detection, per-channel health visibility, and a true escalation path.

## Escalation rule
Delivery-health escalation should use the same enabled operator channels as normal alerts. That keeps the first production delivery system simple:
- if Discord is enabled, escalate there
- if Email is enabled, escalate there too
- do not assume SMS exists until it is actually wired and proven

## Recovery rule
Escalation alone is not enough for unattended trust. After a sent delivery-health escalation, the system should also be able to notify the operator when health returns to normal. Otherwise the operator never gets a closed-loop signal that the incident cleared.

## Delivery trust rule
The dashboard must show enabled channels, recent failures, channel health, recent operator escalations, and the latest recovery posture so the operator can tell whether unattended delivery is actually working. Silent failure is not acceptable.

## Paper-trade lifecycle rule
A paper trade should not just be open or closed. It needs a current operator posture:
- what stage the trade is in
- where the active stop is now
- what target is active now
- whether trailing protection is enabled
- why the trade was closed

This keeps the app useful after entry, not just before entry.

## Usability rule
Trade management should be available in-app on the signal detail page. The operator should not need a separate spreadsheet just to track trailing stops, stop moves, and target progression.

## Spend discipline
Do not spend more on market data or SMS tooling until:
- the scan loop is producing current signals
- Discord and email delivery are both proven in practice
- failed-delivery visibility is good enough to trust unattended runs
- the trade-management loop is usable enough that signals can actually be acted on consistently


## 2026-03-09 Pack d: Held position monitoring
- Added manual held-position tracking so Mike can enter stocks actually purchased.
- Added held-position health checks for stop breach, thesis break via live SHORT signal, target reached, and deep drawdown deterioration.
- Added holdings UI, admin support, dashboard visibility, `check_held_positions`, and scheduler wiring.
- Done: manual position entry/edit/close, health snapshots, delivery-channel alerts, docs refresh.
- Doing: making actual owned-position monitoring a first-class workflow alongside paper trades.
- Left: broker import, CSV import, partial sells, multi-lot tax lots, and richer sell-rule scoring.


## Held-position decision rule
For real owned positions, the app should not just emit alerts. It should convert market state into an operator recommendation bucket that is readable at a glance:
- `sell now`
- `urgent review`
- `review`
- `trim / exit`
- `hold`

That keeps the system usable when Mike needs to decide whether to keep or sell a stock he already bought.

## Sell-discipline rule
A stop breach should immediately escalate the recommendation to `sell now`. A live opposing SHORT signal combined with a losing position should also be treated as a sell-now posture by default, because that is the cleanest first-pass discipline for manual execution.

## Review-before-deterioration rule
Owned positions should get a softer review warning before they hit the deeper deterioration alert threshold. This gives Mike a chance to make a decision before a small loss becomes a larger one.

## 2026-03-09 Pack e: Held-position decision layer
- Added explicit sell/review recommendation buckets for owned holdings.
- Added a warning-drawdown review posture ahead of the deeper deterioration threshold.
- Added a dedicated holding detail page and urgent ranking surfaces.
- Done: decision layer, detail page, urgent visibility, docs refresh.
- Doing: making held-position monitoring a true sell-decision workflow.
- Left: broker import, CSV import, partial exits, multi-lot tax lots, and smarter gain-protection rules.


## Held-position ingestion rule
The app must support a low-friction way to tell it what is already owned. Manual one-by-one entry is not enough once Mike has multiple positions. The first bulk-ingestion step is CSV import with preview-before-apply and symbol validation.

## Import safety rule
Held-position imports should never apply blindly. The operator needs a preview that shows symbol resolution and row-level errors before the import updates live open positions.

## 2026-03-09 Pack f: Held-position CSV import
- Added bulk CSV import with preview-before-apply and flexible header aliases.
- Added create-or-update behavior for open positions by symbol.
- Done: import preview, validation, apply flow, docs refresh.
- Doing: reducing the friction of telling the app what is already owned.
- Left: direct broker sync, tax lots, partial exits, and position reconciliation.


## Price-filter usability rule
Mike should be able to narrow stock lists by price band inside the app. Price matters operationally because cheaper names, mid-priced names, and higher-priced names imply different sizing, risk, and practicality. Signals and held positions should therefore support min/max price filtering without forcing Mike to export to a spreadsheet first.

## Price fallback rule
When filtering by price, the app should use the best available number already in the system instead of requiring one perfect source:
- Signals: trade-plan entry price first, latest timeframe close second
- Held positions: last synced price first, average entry price second

That keeps the price filters usable even when some records are only partially enriched.


## Operator focus-filter rule
Mike should be able to narrow both candidate signals and owned holdings inside the app without exporting anything. The next usable layer after price is focus filtering:
- signals by direction, timeframe, and whether the stock is already held
- holdings by recommendation bucket, source, and open/closed state

This keeps the app practical when Mike wants to answer questions like “show me new long ideas I do not already own” or “show me only sell-now holdings” in one click.


## Saved-preset usability rule
Mike should not have to rebuild the same signal and holding filters every session. Once the app supports meaningful filters, the next usability step is persistent presets that reopen exact operator queues in one click. This keeps the app acting like a real console instead of a raw report page.

## Preset scope rule
The first preset layer should be page-scoped and simple:
- Signals presets save the current signal-filter state
- Holdings presets save the current holding-filter state
- presets are owned by the user and persisted in the DB

That is enough to create daily screeners and sell/review queues without overcomplicating the UI.

## 2026-03-09 Pack i: Saved filter presets
- Added persistent saved presets for Signals and Held Positions.
- Added save-current-filter and one-click preset open/delete workflows.
- Done: filter persistence, operator usability improvement, docs refresh.
- Doing: reducing repeated filter setup work.
- Left: dashboard preset widgets, broker reconciliation, and allocation controls.


## Dashboard-launch rule
Once Mike can save reusable filters, the next usability step is to make the homepage a launch surface for those exact queues. The dashboard should therefore support pinned signal presets and pinned holding presets instead of forcing Mike to click into list pages and rebuild context first.

## Preset pinning rule
Saved filter presets should remain simple, but Mike needs an explicit way to mark which presets deserve homepage placement. A lightweight boolean pin-to-dashboard flag is enough for the first implementation.

## 2026-03-09 Pack j: Dashboard preset widgets
- Added pin-to-dashboard support for saved presets.
- Added homepage widgets for pinned signal presets and pinned holding presets.
- Done: dashboard launch surface for repeated operator queues, docs refresh.
- Doing: reducing homepage-to-decision friction.
- Left: broker reconciliation, allocation controls, and smarter preset metrics.


## Partial-exit usability rule
Once the app can tell Mike to trim or exit, it must also let him record that action directly. A sell recommendation without an in-app way to reduce quantity leaves the monitoring state wrong after Mike acts.

## Position-history rule
Held positions need lightweight transaction history so the app can show how much was sold, at what price, and what realized PnL was captured. The first version can stay single-lot and quantity-based; full tax-lot accounting can come later.

## 2026-03-09 Pack l: Partial-sell workflow
- Added `HoldingTransaction` for lightweight execution history on owned positions.
- Added partial-sell recording and close-via-sale service flow so position quantity stays accurate after Mike trims.
- Added suggested action sizing so a `trim / exit` recommendation includes a concrete quantity and percentage, not just a label.
- Done: transaction history, partial-sell usability, docs refresh.
- Doing: making owned-position review actionable from one page.
- Left: broker reconciliation, multi-lot accounting, and performance analytics.


## Pack 2026-03-09m — Held Position Scale-In / Buy-Add Workflow
- Added an in-app **Record added buy** workflow for open held positions.
- The app now recalculates **quantity** and **weighted average entry price** after you add shares to an existing holding.
- Added a new holding transaction event type: **BUY_ADD**.
- Holding detail now supports the full real-world loop in one place: add shares, partial sell, or close.
- This improves usability for scaling into a position instead of treating every holding as static after the first purchase.


## Performance review rule
Once the app can record real adds, trims, and closes, it needs a first-class way to review realized vs unrealized performance. Otherwise Mike can manage positions in-app but still has to leave the app to understand whether the process is improving or degrading.

## Pack 2026-03-09n: Holdings performance analytics
- Added a dedicated holdings performance page for realized/unrealized review.
- Added realized win-rate, top open winners/losers, and recent closed holdings so execution quality can be reviewed without exporting data first.
- Done: performance review surface, dashboard visibility, docs refresh.
- Left: multi-lot accounting, broker reconciliation, and deeper attribution by strategy or setup.

## 2026-03-09o — Import reconciliation is review-first, not auto-close
- Later account CSV imports may be incomplete or exported with filters, so missing symbols should not automatically close positions.
- The system now flags open holdings absent from the latest import with a reconciliation marker instead of mutating quantity to zero or force-closing them.
- This keeps the workflow conservative: surface drift first, then let Mike decide what to close or fix.

## 2026-03-09 — Import reconciliation should be explicit, not implied
- Holdings missing from a later account CSV should not be auto-closed.
- The operator must explicitly decide whether the absence means “still held but omitted” or “position actually exited elsewhere.”
- The app now records a reconciliation note and resolution timestamp so account-maintenance decisions remain auditable.


## 2026-03-09 — Calibration should be visible in-app
- Added a dedicated analytics screen instead of leaving score/outcome review buried in admin or raw tables.
- Decision: score buckets should be compared against both closed paper trades and evaluated signal outcomes, because one shows execution-style results while the other shows raw setup behavior.
- Decision: include timeframe, strategy, and minimum-sample filters so Mike does not overfit to tiny buckets.


## 2026-03-09 Pack r: Score-band usability
- Added direct score-band filters to Signals instead of forcing Mike to infer quality from the table after loading everything.
- Added conviction summary cards and quick score-band links so day-to-day review starts with the highest-quality part of the queue first.


## 2026-03-09 — Watchlist management should be in the operator workflow
- The scan universe is a day-to-day operator concern, not just a bootstrap/admin concern.
- Mike should be able to add or remove symbols from the active watchlist directly from the Signals and Holdings screens when a name becomes more or less interesting.
- First implementation stayed simple: one active watchlist, inline add/remove, and manual symbol add from the existing instrument universe.


## 2026-03-09 — Watchlist import should be preview-first
- The watchlist is part of the operator workflow, so bulk changes should not apply blindly.
- Decision: default-watchlist imports must preview symbol resolution before any activate/deactivate change is committed.
- Decision: exact-sync behavior should be optional, not automatic, so Mike can choose between additive imports and full watchlist replacement.
- Decision: duplicate symbols inside the same import should be skipped after the first ready occurrence instead of causing a hard failure.


## 2026-03-09 Pack u — Multi-watchlist operator flow
- Decision: watchlists are now a first-class operator concept, not a hidden single default list.
- Decision: one watchlist per user is marked active and drives the current scan universe shown across Dashboard, Signals, Holdings, and Watchlist actions.
- Decision: creation/usability comes first, so named watchlists and active switching are in-app now; deeper broker-fed universe sync can come later.


## 2026-03-09 Pack v — Watchlist priority is an operator aid, not a trading signal
- Decision: active watchlist symbols now support `High`, `Normal`, and `Low` priority plus a short operator note.
- Priority is meant to improve daily review order and explain why a symbol is in the universe; it does not override score, recommendations, or held-position logic.
- The dashboard only surfaces the count of high-priority names so the homepage stays lightweight while still exposing the most actionable slice of the watchlist.


## 2026-03-10 — Sector/theme tags belong on the watchlist selection, not the instrument master
- Sector/theme buckets in this app are an operator workflow tool, not necessarily a permanent global truth about the instrument.
- Decision: store the sector/theme on `InstrumentSelection` so Mike can classify the same symbol differently in different watchlists if needed.
- Decision: sector summaries should be derived from the latest non-flat signal per symbol so the watchlist board reflects current operator posture, not just static membership.


## 2026-03-10 — Holdings sector exposure uses active watchlist tags
- Sector/theme grouping for owned holdings is derived from the **active watchlist** rather than a second classification table.
- Reason: Mike is already curating sector/theme labels there, so reusing that source keeps the workflow simple and editable in-app.
- Consequence: exposure quality depends on watchlist tagging discipline; untagged names fall into **Unassigned** until labeled.


## 2026-03-10 — Concentration limits are soft operator guardrails
- Single-position and sector/theme caps live in `UserRiskProfile` because they are operator-specific account settings.
- Caps are advisory review surfaces for now; the app does not auto-liquidate or block actions solely because a cap is exceeded.
- Sector exposure continues to derive from active-watchlist sector/theme tags so Mike can control the taxonomy instead of relying on a third-party classifier.


## 2026-03-10 — Pre-trade guardrails use projected account-equity weights
- Candidate trades now evaluate projected single-position weight and projected sector/theme weight before entry.
- For pre-trade checks, the denominator is account equity from `UserRiskProfile`, not only current held market value. This keeps the guardrail check aligned with real entry sizing and cash headroom.
- Sector/theme projections reuse the active watchlist tags so Mike does not need a second classification system for pre-trade concentration review.


## 2026-03-11 — Correlation clustering is a separate guardrail from sector caps
- Sector caps catch taxonomy crowding, but they do not catch cases where different symbols still move almost the same way.
- Decision: pre-trade review now checks recent daily-return correlation between a candidate signal and currently held names.
- Decision: the first version is a soft operator guardrail driven by `UserRiskProfile` settings for threshold, lookback bars, and how many strongly correlated names are acceptable before a new trade is over-limit.
- Decision: this remains intentionally lightweight for now; deeper factor models and true portfolio optimization can come later after the basic cluster check proves useful.

## 2026-03-11 Pack am: Correlation cluster guardrails
- Added rolling-correlation checks for candidate trades versus current holdings.
- Added risk-profile controls for correlation threshold, lookback window, and allowed correlated-name count.
- Done: correlation-aware pre-trade posture, Signals/Dashboard visibility, docs refresh.
- Doing: reducing duplicate-risk entries before Mike adds another position.
- Left: portfolio net exposure posture, broker/account reconciliation, and deeper correlation attribution.


## 2026-03-11 — Portfolio deployment needs its own cap separate from single-name and sector caps
- A trade can fit single-position, sector, and correlation guardrails while still pushing the whole account too far into the market.
- Decision: add a soft `max_net_exposure_pct` guardrail on `UserRiskProfile` plus a separate warning buffer for portfolio-level posture.
- Decision: in the current long-only holdings workflow, net exposure is intentionally treated the same as deployed long exposure. This keeps the concept useful now while leaving room for future short or hedge support.
- Decision: candidate-trade guardrails should show projected net exposure after entry so Mike can see when a setup is good on its own but still too aggressive for current portfolio posture.

## 2026-03-11 Pack an: Net exposure posture
- Added `max_net_exposure_pct` and `net_exposure_warning_buffer_pct` to `UserRiskProfile`.
- Added exposure-summary posture for current portfolio deployment.
- Added pre-trade projected net exposure checks inside candidate signal guardrails.
- Done: portfolio-level deployment cap, UI visibility, docs refresh.
- Left: short-aware net exposure, broker-fed posture, and deeper optimization logic.


## 2026-03-11 — Broker/account posture should start with safe snapshots, not credentials
- The next step toward reconciliation is not storing broker logins or placing trades. It is comparing the app's tracked holdings/exposure against an external account snapshot.
- Decision: add `ImportedBrokerSnapshot` for manual or exported account totals (equity + cash + notes + as-of timestamp).
- Decision: keep `UserRiskProfile.account_equity` as the operator sizing control for now; broker snapshots are an external reference used to surface drift, not an automatic override.
- Decision: show drift between tracked held market value and broker-implied invested capital (`equity - cash`) so Mike can spot missing positions, stale prices, or incomplete imports before direct broker sync exists.

## 2026-03-11 Pack ao: Broker/account snapshot posture
- Added `ImportedBrokerSnapshot` with user/source/as-of/equity/cash/notes.
- Added allocation-screen and dashboard posture for latest broker snapshot drift versus tracked holdings.
- Done: safe reconciliation baseline without credentials, docs refresh.
- Left: direct broker sync, lot-level reconciliation, and automated external feed handling only after the snapshot workflow proves useful.


## 2026-03-11 — Broker reconciliation should be review-first at the position level too
- Account-level equity/cash drift is useful, but Mike still needs a symbol-by-symbol workflow before direct broker sync is worth the complexity.
- Decision: add a broker/export position CSV preview that compares imported symbol quantities against tracked open holdings without mutating anything automatically.
- Decision: the first version is intentionally conservative: show exact matches, quantity mismatches, broker-only symbols, and tracked-only symbols, then let Mike decide whether to import, edit, or close holdings from the existing workflows.
- Decision: market-value totals from the broker CSV are optional and used only as reconciliation context, not as an automatic override of tracked holding prices or quantities.

## 2026-03-11 Pack ap: Broker position reconciliation preview
- Added a review-first broker/export position CSV parser with support for `symbol`, `quantity`, optional `market_price`, optional `market_value`, and optional `average_entry_price`.
- Added a dedicated reconciliation page linked from Allocation Controls that compares imported broker positions against tracked open holdings.
- Done: exact-match visibility, quantity-mismatch queue, broker-only/tracked-only mismatch lists, docs refresh.
- Left: direct broker sync, persistent broker-position import history, and lot-level reconciliation.


## 2026-03-11 — Broker reconciliation history must be auditable
- Preview-only mismatch review is not enough once broker/account reconciliation becomes part of a repeated operator process.
- Decision: every broker/export reconciliation preview now creates a durable `BrokerPositionImportRun` with stored row preview data and mismatch summary counts.
- Decision: symbol-level mismatch decisions are stored as `BrokerPositionImportResolution` records with action + note, instead of disappearing into chat history or implicit memory.
- Decision: this remains review-first; saved resolutions document what Mike decided, but they do not automatically mutate holdings unless a separate explicit workflow is used.

## 2026-03-11 Pack aq: Persistent broker reconciliation history
- Added durable run history, unresolved issue counts, and symbol-level resolution tracking for broker/export reconciliation.
- Done: saved review runs, resolution notes, allocation-controls visibility, docs refresh.
- Doing: making reconciliation an auditable workflow.
- Left: optional apply-actions after review, true broker account sync, and multi-account posture.


## 2026-03-11 — Reconciliation apply actions should open forms, not commit changes
- Once mismatch review is saved, the next operator step should be fast, but still explicit.
- Decision: broker reconciliation now offers apply buttons that deep-link into the existing add-holding, add-shares, partial-sell, or close workflows with prefilled quantities/prices/notes.
- Decision: these links must not auto-save holdings or auto-mark the mismatch resolved. Mike still reviews and submits the actual form so the workflow stays conservative and auditable.
- Consequence: reconciliation becomes faster without crossing into hidden broker-sync behavior.

## 2026-03-11 Pack ar: Import-assisted apply workflows
- Added prefilled workflow links from broker reconciliation issues into the existing holding forms.
- Added GET-driven prefill support on add-holding, add-shares, partial-sell, and close-position screens.
- Done: apply-assist usability, docs refresh.
- Left: optional apply+resolve shortcuts after further validation, live broker sync, and multi-account posture.


## 2026-03-11 Pack as: Apply + resolve shortcuts
- Added optional broker reconciliation shortcuts that prefill the correct holding workflow and carry explicit resolve intent forward to the final submit step.
- Added apply-and-resolve handling for add holding, add shares, partial sell, and full close flows.
- Kept the workflow conservative: nothing is auto-mutated from the reconciliation screen itself, and the mismatch only resolves after the actual holding transaction is submitted successfully.
- Done: explicit apply+resolve path, run-detail shortcuts, docs refresh.
- Doing: reducing reconciliation clicks while keeping a visible operator confirmation step.
- Left: richer reconciliation notes/history and eventual broker connectivity only after trust and audit posture stay strong.


## 2026-03-11 — Broker reconciliation history should be filterable and easier to explain
- Once saved reconciliation runs accumulate, operator trust depends on being able to isolate unresolved items, accepted quantity differences, and follow-up decisions quickly.
- Decision: keep broker reconciliation review inside the app with simple run-detail filters for issue bucket, resolution status, and saved action rather than pushing this into admin-only tooling.
- Decision: add operator-friendly note presets for common mismatch explanations so reconciliation notes stay more consistent and auditable over time.
- Decision: show recent resolution history and action breakdowns on the saved run itself so Mike can review what was already decided before making another holding adjustment.

## 2026-03-11 Pack at: Reconciliation review ergonomics
- Added run-detail filters for mismatch bucket, resolution status, and resolution action.
- Added resolution note presets, recent resolution history, and action summary badges.
- Fixed Allocation Controls context for recent broker reconciliation runs.
- Done: richer reconciliation notes/history ergonomics, docs refresh.
- Doing: tightening operator audit flow before any live broker connection work.
- Left: multi-account posture, lot-level reconciliation, and eventual live broker sync only after the manual workflow remains trustworthy.


## 2026-03-11 — Broker snapshots and reconciliation runs need account labels before holdings do
- Once Mike uses more than one broker account, a single blended snapshot/reconciliation history becomes ambiguous even if the underlying holdings book is still combined.
- Decision: add optional `account_label` to `ImportedBrokerSnapshot` and `BrokerPositionImportRun` first, because that preserves audit clarity without forcing an immediate holdings-model redesign.
- Decision: Allocation Controls should support an account filter, but the default posture should also show the combined latest-per-account view so the dashboard still has one top-line broker drift number.
- Consequence: tracked holdings remain one blended internal book for now, so combined broker posture is an approximation until held positions themselves can be attributed to an account.

## 2026-03-11 Pack au: Multi-account broker posture
- Added account labels to broker snapshots and saved reconciliation runs.
- Added account-filtered recent snapshot/run review plus combined latest-per-account drift posture.
- Done: multi-account broker grouping, dashboard/allocation visibility, docs refresh.
- Left: tracked-holding account attribution, lot-level reconciliation, and eventual live broker sync.


## 2026-03-11 — Tracked holdings now support optional broker-account attribution
- Broker snapshots and reconciliation runs already had account labels, so leaving tracked holdings unlabeled would keep per-account review only half-connected.
- Decision: `HeldPosition` now carries an optional `account_label` so the same symbol can be tracked as belonging to a specific broker account when Mike wants that fidelity.
- Decision: account labels stay operator-managed text for now instead of a separate account model. This keeps the workflow light and matches the existing snapshot/reconciliation posture.
- Decision: holding CSV imports and broker reconciliation comparisons should scope missing-position review to the selected account label instead of flagging the entire blended book.
- Consequence: unlabeled holdings still work exactly like before, while labeled holdings unlock cleaner per-account posture and drift review without forcing a hard migration to a full broker-account system.


## 2026-03-12 — Account moves should be explicit audit events, not fake trades
- Once holdings can belong to broker accounts, Mike also needs a safe way to move or relabel them when the tracking book was assigned to the wrong account or when a broker/account label changes.
- Decision: account moves should be recorded as their own `HoldingTransaction` event type instead of pretending the move was a buy, sell, or close.
- Decision: the first version stays simple and operator-driven: one holding record keeps its quantity/cost basis, only the `account_label` changes, and the transfer note captures why.
- Decision: leaving the target account blank is allowed and explicitly means "move this holding back to the blended / unlabeled book."
- Consequence: the audit trail stays truthful, and later account-level posture can rely on cleaner account assignments without muddying execution history.

## 2026-03-12 Pack aw: Holding account transfer / relabel workflow
- Added an in-app move/relabel form on holding detail for open tracked positions.
- Added `ACCOUNT_TRANSFER` transaction history so account changes are visible in the same audit trail as opens, adds, trims, and closes.
- Done: audit-safe account reassignment, docs refresh.
- Left: account-level posture, lot-level reconciliation, and eventual live broker sync.


## 2026-03-12 — Account-level posture should be reviewed explicitly
- Once holdings, broker snapshots, and reconciliation runs can all carry account labels, the app should expose a per-account risk posture instead of only a blended portfolio posture.
- Decision: per-account posture should combine tracked market value, deployment against an equity basis, broker drift, and unresolved reconciliation issues in one operator summary.
- Decision: use the latest broker snapshot equity as the preferred account-equity basis when available; fall back to the single-account allocation profile only when the user is effectively tracking one account.
- Reason: a blended portfolio total can hide which specific account is overdeployed, drifting, or still unresolved after reconciliation review.


## Account exposure heatmap rule
Once holdings, broker snapshots, and reconciliation can all belong to accounts, concentration review also needs to be account-aware. Mike should be able to see which account is hottest by sector crowding and which account is carrying the heaviest single-name risk without mentally combining multiple tables.

## Heatmap posture rule
The first account exposure heatmap stays review-first:
- it highlights top sector weight per account
- it highlights top position weight per account
- it rolls those into a simple over / near / ok heat posture
- it does not auto-trim, auto-close, or auto-transfer anything

## 2026-03-12 Pack ay: Account-level exposure heatmap
- Added account-level concentration board visibility on Allocation Controls.
- Added compact dashboard heatmap visibility for top sector and top single-name pressure by account.
- Done: exposure heatmap, docs refresh.
- Left: account-level drawdown monitoring and explicit stop-loss guardrails.


## 2026-03-12 — Account drawdown posture should be visible per account
- Once holdings can belong to accounts, drawdown review should also be account-aware instead of blended.
- Decision: reuse the existing held-position warning and deterioration thresholds for the first account-level drawdown board so there is one consistent definition of warning vs deep stress.
- Decision: the first version stays review-first and surfaces posture, counts, and worst names only; it does not auto-sell, auto-close, or auto-transfer anything.
- Reason: Mike needs to see stress building inside a specific broker bucket before it turns into reactive selling, but the app should not jump straight from posture to execution.

## 2026-03-12 Pack az: Account-level drawdown monitoring
- Added Allocation Controls and dashboard visibility for per-account drawdown posture.
- Added worst-account / worst-name surfacing plus warning and deep-drawdown counts by account.
- Done: account-level drawdown visibility, docs refresh.
- Left: explicit stop-loss guardrails and deeper account drill-downs.


## 2026-03-12 — Stop-loss guardrails should be explicit and operator-configurable
- Account-level drawdown posture is useful, but Mike still needs the app to say what specific stop-discipline problem exists on a holding: missing stop, stop too wide, near stop, or urgent drawdown.
- Decision: move the first stop/risk controls into `UserRiskProfile` so the workflow stays operator-managed alongside the existing allocation controls.
- Decision: the first guardrail set is review-first and does **not** auto-liquidate anything. It only upgrades visibility and tells Mike what to fix.
- Decision: treat missing stops and stops that are wider than the configured loss budget as **over-limit** posture, and treat near-stop or review-drawdown conditions as **near-limit** posture.
- Consequence: the app can now surface stop-discipline debt directly on holding detail, the holdings list, Allocation Controls, and the dashboard without pretending to be a broker or taking hidden actions.

## 2026-03-12 Pack ba: Explicit stop-loss / risk guardrails
- Added stop-discipline settings to `UserRiskProfile` for required stops, max stop width, near-stop warning buffer, and review/urgent drawdown levels.
- Added explicit per-holding risk-guardrail posture with labels and actions.
- Added holding-level and account-level guardrail boards on Allocation Controls plus compact dashboard visibility.
- Done: explicit stop/risk operator actions, docs refresh.
- Left: deeper account drill-downs and richer stop-discipline history.


## 2026-03-12 — Stop-discipline history should be captured at transaction time
- Current guardrail posture is useful, but once Mike adds, trims, or closes a holding the app should preserve what the stop discipline looked like **when that action happened**.
- Decision: snapshot account label, stop price, and guardrail posture onto `HoldingTransaction` instead of trying to reconstruct it later from the current holding state.
- Reason: current holding state can change after the fact, which would make historical discipline review unreliable.
- Consequence: old transactions remain blank until new actions are recorded, but all new execution events become audit-friendlier without introducing hidden automation.

## 2026-03-12 — Account posture must drill into queues, not just summary badges
- Per-account posture is useful, but Mike also needs to see what work is actually waiting inside each account.
- Decision: add account-specific holding queues that surface sell-now, review-now, trim, missing-import, missing-stop, and stop-breached counts.
- Decision: keep the first version review-first by linking into filtered holdings queues rather than auto-mutating or auto-routing trades.
- Reason: summary posture alone can hide where the next manual action should happen.

## 2026-03-12 Pack bb: Account holding queues + stop-discipline history
- Added account queue drill-downs on Allocation Controls and compact dashboard visibility.
- Added durable stop/guardrail snapshots on new holding transactions.
- Done: queue-level multi-account review, durable discipline snapshots, docs refresh.
- Left: trend analytics over longer time windows and optional broker-account model hardening.


## 2026-03-12 — Stop-discipline should be measured as a trend, not just a snapshot
- A single list of recent guardrail events is useful for audit, but it does not tell Mike whether execution hygiene is getting better or worse.
- Decision: derive stop-discipline trend analytics from `HoldingTransaction` snapshots that were already added for opens, adds, trims, and closes.
- Decision: the first trend surface should stay lightweight: compare recent 30-day hygiene and debt rates against the prior 30-day window, while also showing 7-day and 90-day context.
- Decision: treat over-posture events plus missing-stop opens/adds as the first version of stop-discipline debt so the app highlights the clearest process failures first.
- Consequence: this remains an operator feedback layer, not a compliance engine or auto-execution trigger.


## 2026-03-12 — Stop policy should measure response time, not only posture
- A stop-discipline snapshot is useful, but Mike also needs to know how long new opens/adds stay without a recorded or tightened stop.
- Decision: add a configurable `stop_policy_target_hours` setting to `UserRiskProfile` so timeliness stays explicit and operator-controlled.
- Decision: store stop-policy timing directly on `HoldingTransaction` for `OPEN` and `BUY_ADD` events (`due_at`, `resolved_at`, `status`) instead of trying to infer it later from the current holding state.
- Decision: treat a stop added later through holding edit, import sync, or add-shares-with-stop as the resolution event for the oldest pending stop-policy items on that holding.
- Consequence: the app can now show on-time rate, pending debt, and average time-to-stop by account without pretending it knows broker-side order placement.

## 2026-03-12 Pack bd: Stop-policy timeliness analytics
- Added stop-policy target-hours configuration, timing snapshots on open/add transactions, and stop-resolution tracking when stops are later added or tightened.
- Added Allocation Controls and dashboard visibility for on-time stop follow-through by account.
- Done: stop-policy timeliness analytics, docs refresh.
- Left: richer drill-downs and optional broker-order linkage only if manual trust remains strong.


## 2026-03-12 — Stop-policy debt needs a working queue, not just a scorecard
- Timeliness analytics alone are not enough once the app starts tracking stop-policy debt. Mike needs a direct place to work pending and overdue stop follow-through by account.
- Decision: add a dedicated stop-policy follow-up queue with account, event-type, and status filters instead of burying the debt inside Allocation Controls summary cards.
- Decision: the queue remains review-first. It should open the real holding edit workflow so stop changes are still explicit and auditable.
- Decision: keep the first queue centered on open/add events only, because those are the stop-policy events with a defined operator SLA today.


## 2026-03-12 — Stop-policy exceptions need explicit audit reasons
- Decision: store stop-policy reason code and freeform note directly on `HoldingTransaction` so Mike can explain late or intentionally deferred stop follow-through on the exact open/add event that created the debt.
- Decision: keep the first version lightweight and operator-driven from the follow-up queue instead of adding a separate exception model. The timing event is already the durable audit anchor; it just needed reason metadata.
- Decision: expose those notes in both the follow-up queue and holding transaction history so exception handling remains visible later during review.


## 2026-03-12 — Stop-policy exception trends must be reviewable as patterns, not just row notes
- Reason codes and notes are useful only if Mike can see whether the same explanations keep repeating over time.
- Decision: compare reason-code usage across recent 30-day and prior 30-day windows instead of only listing current rows.
- Decision: flag recurring symbols over a 90-day window so repeated defer behavior surfaces even when individual notes looked justified in isolation.
- Decision: keep this operator-facing and review-first; repeated exception patterns do not auto-mutate holdings or force any stop changes.


## 2026-03-12 — Repeated-symbol exception names should open cleanup flows, not stop at reporting
- Recurring stop-policy exceptions are only useful if Mike can push the same symbol directly into a tighter remediation queue.
- Decision: the stop-policy follow-up screen now accepts a symbol filter so repeated names can be isolated without losing account/event context.
- Decision: recurring-symbol trend rows should expose the current posture and a direct next action (`Record / tighten stop` when still actionable, otherwise review detail/history).
- Decision: this remains review-first. The app surfaces the right cleanup path but still requires Mike to submit the actual holding edit rather than auto-mutating stop values.


## 2026-03-12 — Reason codes should open playbooks, not just annotate exceptions
- Once stop-policy reason codes exist, Mike needs the app to route repeated reasons into the right workflow instead of forcing him to interpret every row from scratch.
- Decision: add reason-code remediation playbooks that recommend the right queue posture per reason (for example overdue confirmation checks vs all-history defer audits).
- Decision: extend the stop-policy follow-up queue with a reason filter so playbooks can open directly into the correct subset of rows while preserving account and event context.
- Decision: keep the workflow review-first. Playbooks can route Mike into the right filtered queue and holding edit screen, but they do not auto-tighten stops or auto-resolve exceptions.

## 2026-03-12 Pack bi: Reason-code remediation playbooks
- Added reason-filtered queue routing and remediation playbooks on the follow-up page.
- Added top-playbook visibility on Allocation Controls and Dashboard.
- Done: reason-level cleanup workflows, docs refresh.
- Left: broader SLA / operational reporting by reason and any later broker-linked execution confirmation only if needed.


## 2026-03-13 — Reason codes need SLA visibility, not just routing
- Once reason-code playbooks exist, Mike still needs to know which reasons are staying open too long instead of only which reasons occur most often.
- Decision: add reason-level SLA reporting using actionable count, overdue rate, and oldest open age so the app surfaces the stalest operational debt first.
- Decision: show the top stale reason on Allocation Controls, the follow-up queue, and the dashboard so backlog pressure is visible without opening every detail view.
- Decision: keep this review-first. SLA reporting routes Mike into the right filtered queue but does not auto-resolve or auto-tighten any stops.

## 2026-03-13 Pack bj: Reason-code SLA / operations reporting
- Added reason-level aging and overdue reporting for stop-policy exceptions.
- Added top-stale-reason summaries and direct SLA queue links.
- Done: operational backlog visibility, docs refresh.
- Left: optional broker-linked execution evidence and any later automation only after the manual workflow stays trustworthy.

## 2026-03-13 — Waiting-for-confirmation exceptions need explicit evidence, not just notes
- A `WAITING_CONFIRMATION` reason is only trustworthy if Mike can show what confirmation he is waiting on or what broker evidence he already saw.
- Decision: store execution evidence directly on `HoldingTransaction` (`execution_evidence_type`, `execution_evidence_reference`, `execution_evidence_note`, `execution_evidence_recorded_at`) instead of creating a separate evidence model first.
- Decision: keep the first version lightweight and inline on the stop-policy follow-up queue. Mike can record order IDs, broker confirmations, import matches, or manual verification evidence without leaving the queue.
- Decision: surface evidence-backed counts in the queue, SLA reporting, Allocation Controls, and Dashboard so the app can distinguish supported waiting-for-confirmation exceptions from genuinely unsupported follow-through debt.

## 2026-03-13 Pack bk: Broker-order confirmation / execution evidence capture
- Added inline execution-evidence capture and evidence-aware filtering/reporting for stop-policy exceptions.
- Added durable evidence fields to `HoldingTransaction` with a recorded timestamp.
- Done: evidence-backed confirmation workflow, docs refresh.
- Left: evidence trend reporting, optional file attachments later, and any future broker automation only after the manual process remains trustworthy.

## 2026-03-13 — Execution evidence should be reviewable as trends, not just row fields
- Once execution evidence exists, Mike needs to know whether confirmation-backed exceptions are improving over time or whether unsupported rows are still piling up.
- Decision: add evidence-type trend reporting using 30-day volume, delta vs prior 30 days, supported vs unsupported mix, and actionable pressure.
- Decision: add evidence-type filtering to the stop-policy follow-up queue so evidence trend rows can route directly into the matching backlog.
- Decision: keep this review-first. Evidence trends expose where confirmation quality is improving or degrading, but they do not auto-resolve stop-policy exceptions.

## 2026-03-13 Pack bl: Execution-evidence trend reporting
- Added evidence-type trend reporting and unsupported-waiting-confirmation summaries for stop-policy exceptions.
- Added evidence-type queue routing on the stop-policy follow-up page plus Allocation Controls and Dashboard summaries.
- Done: evidence trend visibility, evidence-type filtering, docs refresh.
- Left: optional attachment support, stronger evidence validation, and any future broker automation only after the manual workflow remains trustworthy.

## 2026-03-13 — Execution evidence needs explicit quality, not just presence
- Once execution evidence exists, Mike still needs to know whether the evidence is actually trustworthy. A broker confirmation and a placeholder note should not be counted the same way.
- Decision: store `execution_evidence_quality` directly on `HoldingTransaction` so quality travels with the exact stop-policy event that created the exception.
- Decision: use lightweight quality levels (`VERIFIED`, `STRONG`, `WEAK`, `PLACEHOLDER`) and allow an unrated state for older evidence rows that were recorded before quality was captured.
- Decision: surface evidence-quality backlog and queue filters alongside evidence-type reporting so Mike can open the weak / placeholder debt directly without auto-mutating anything.

## 2026-03-13 Pack bm: Evidence-quality controls
- Added execution-evidence quality grading, queue filters, and evidence-quality reporting across stop-policy follow-up, Allocation Controls, and Dashboard.
- Done: trust-weighted evidence review, docs refresh.
- Left: optional evidence attachments, stronger validation rules, and any future broker automation only after the manual workflow remains trustworthy.


## 2026-03-13 — Execution evidence should support attachments, not just text fields
- Some confirmation evidence is materially stronger when Mike can attach the actual screenshot, broker PDF, or CSV snippet instead of only typing a note or reference number.
- Decision: add `execution_evidence_attachment` directly to `HoldingTransaction` so the file stays attached to the exact stop-policy event being audited.
- Decision: keep attachment capture inline on the stop-policy follow-up queue rather than creating a separate evidence object first.
- Decision: treat attachments as audit support only. They improve trust and reviewability, but they do not auto-resolve stop-policy exceptions.

## 2026-03-13 Pack bn: Evidence attachment support
- Added inline evidence attachment upload plus attachment visibility on the stop-policy follow-up queue.
- Added durable file-backed evidence storage on `HoldingTransaction` and surfaced attachment-aware evidence counts.
- Done: file-backed evidence capture, docs refresh.
- Left: stronger validation/retention rules, cleanup policies, and any future broker automation only after the manual workflow remains trustworthy.


## Pack BO — Evidence Validation + Retention Controls
- Decision: validate evidence uploads at the form layer first, using an allowlist of PDF, PNG, JPG, WEBP, CSV, and TXT plus a 5 MB limit, instead of accepting arbitrary files.
- Decision: keep retention lightweight for v1 by storing a single `execution_evidence_retention_until` timestamp on `HoldingTransaction` rather than creating a separate evidence-retention model.
- Decision: default new evidence attachments to a 365-day retention window so Mike can see what is expiring soon before deciding whether to archive, replace, or delete it later.
- Added retention visibility to stop-policy follow-up and high-level summaries.

## Pack BP — Evidence retention work queues / archive workflow
- Decision: keep evidence retention actions row-based and explicit for now. Mike should consciously extend retention or archive/clear the file rather than having background lifecycle rules mutate evidence silently.
- Decision: archive workflow clears the attachment but preserves the note/reference audit trail on the same `HoldingTransaction` row so the exception history remains understandable after the file is removed.
- Decision: add lightweight extension actions (+90d / +365d) instead of introducing a separate archive model before the operational workflow proves itself.


## Pack BQ — Bulk retention actions / archive batching
- Decision: keep bulk retention actions scoped through the existing stop-policy follow-up filters instead of adding a separate mass-edit screen. This keeps batching review-first and tied to the queue Mike is already working.
- Decision: the first bulk version supports only explicit retention actions on attachment-backed evidence rows: extend 90 days, extend 365 days, or archive / clear file.
- Decision: archive batching remains audit-safe. Removing the attachment never deletes the underlying stop-policy transaction row or its note/reference trail.


## Evidence-retention preset rule
Attachment-backed execution evidence should not always receive one fixed retention window. The default retention date should be derived from Allocation Controls using the strongest applicable preset across evidence quality and evidence type.

## Evidence-retention priority rule
When multiple preset categories apply, the system should choose the longest relevant retention window rather than the shortest one. This keeps higher-trust or externally corroborated evidence available at least as long as weaker evidence, while still letting Mike manually extend or archive rows later.

## 2026-03-14 Pack br: Retention-policy presets
- Added configurable evidence-retention presets on `UserRiskProfile`.
- Added preset-based retention assignment when a new execution-evidence attachment is saved from the stop-policy follow-up queue.
- Added stop-policy follow-up visibility for the active preset windows so the current storage policy is obvious during review.
- Done: configurable default/quality/type windows, preset-based attachment retention, docs refresh.
- Left: dashboard-level preset summaries and any future per-account override logic.

## Pack BT — Per-account retention overrides
- Decision: keep per-account evidence retention in a separate `AccountRetentionPolicyOverride` model keyed by `(user, account_label)` instead of adding more columns directly to `UserRiskProfile`. The global profile stays the default layer; account overrides remain sparse and optional.
- Decision: account overrides should affect only future retention assignment by default. They do not silently rewrite historical `execution_evidence_retention_until` values because that would mutate audit history after the fact.
- Decision: only labeled accounts can receive overrides. The blended/unlabeled book continues to use the global retention presets so the default path stays simple.
- Added Allocation Controls CRUD for account-specific override windows and wired stop-policy attachment saves to consult the account override first.
- Done: optional account-level policy layer, forward-looking retention assignment, docs refresh.
- Left: optional dashboard visibility and any future bulk re-baseline tooling for historical evidence rows.

## Pack BU — Dashboard visibility for account-level retention override posture
- Decision: dashboard visibility for retention overrides should be read-only and derived from active account usage, not from the raw override table alone. That keeps the homepage focused on accounts actually in play.
- Decision: active account posture should aggregate account labels found in open held positions, broker snapshots, and broker reconciliation runs so the retention board reflects real operational context instead of one narrow source.
- Decision: editing remains in Allocation Controls. The dashboard only summarizes custom-vs-global posture and window ranges; it does not rewrite historical evidence rows or policy defaults.
- Added a shared `summarize_account_retention_override_posture(...)` summary and wired the dashboard to show custom/global counts plus a compact per-account posture table.
- Done: homepage visibility, shared summary source, docs refresh.
- Left: copy/clone helpers and any future account-family templating.


## Pack BV — Copy / clone helpers for account retention overrides
- Decision: clone helpers should copy only the sparse `AccountRetentionPolicyOverride` row for the target account. They do not backfill or rewrite historical `HoldingTransaction.execution_evidence_retention_until` values.
- Decision: clone workflow should be explicit and reviewable inside Allocation Controls. A target account with an existing override is blocked unless Mike explicitly chooses overwrite.
- Decision: row-level **Clone** actions should only preselect the source override in the clone form; the actual copy still requires an explicit submit so nothing mutates from a plain link click.
- Added `AccountRetentionPolicyOverrideCloneForm`, risk-settings clone handling, and a clone action on existing override rows.
- Done: clone/copy workflow, overwrite safety check, docs refresh.
- Left: optional account-family templates and any future policy recommendation tooling.


## Pack BW — Account-family retention templates
- Decision: reusable family templates belong in a separate `AccountRetentionPolicyTemplate` model rather than overloading `AccountRetentionPolicyOverride`. Overrides remain the per-account execution layer; templates are the reusable seed layer above them.
- Decision: applying a template should create or update target account overrides only. It must not mutate historical `HoldingTransaction.execution_evidence_retention_until` values because those are part of the audit trail.
- Decision: bulk template apply should accept plain account labels entered as comma- or newline-separated text so Mike can seed several related accounts without setting up extra structure first.
- Added template CRUD plus bulk apply tooling in Allocation Controls and a summary board for template families.
- Done: family-template layer, multi-account apply workflow, docs refresh.
- Left: optional policy recommendation helpers and any future drift-detection/reporting tied to template expectations.


## Pack BX — Template recommendation helpers
- Decision: recommendation helpers should stay advisory and prefill-only. The app can suggest a template apply or a save-as-template action, but it should not silently create overrides or templates from pattern matches.
- Decision: active-account recommendations should be derived from accounts already in live use across held positions, broker snapshots, and broker reconciliation runs. That keeps the suggestions tied to operational reality instead of dormant configuration.
- Decision: repeated override patterns are sufficient for the first recommendation pass. If multiple account overrides share the same retention signature and no reusable template exists yet, the app should suggest saving one of those overrides as a family template.
- Added a shared recommendation summary plus Allocation Controls boards for uncovered accounts, reusable template applies, and repeated-pattern template candidates.
- Done: advisory recommendation layer, prefilled apply links, docs refresh.
- Left: optional drift reporting between live account evidence mix and the intended template policy.


## Pack BY — Template drift reporting
- Decision: per-account overrides need optional `source_template` lineage so drift can be measured against the template that originally seeded the override. Without lineage, drift reporting becomes guesswork based only on matching windows.
- Decision: drift reporting should stay visibility-first. It compares current override windows to the linked template, counts changed fields, and shows lightweight evidence posture so Mike can judge whether drift matters operationally.
- Added `AccountRetentionPolicyOverride.source_template`, template-lineage stamping during template apply, clone-lineage preservation, a drift summary service, and compact dashboard/risk-settings drift surfaces.

## Pack BZ — Drift remediation helpers
- Decision: remediation should be explicit and audit-safe. `Reset to template` rewrites only the current override row back to the linked template values; it does not rewrite historical evidence retention dates. `Detach` removes template lineage but preserves the current override windows exactly as they are.
- Reason: Mike needs a clean way to either rejoin a family policy or intentionally fork an account without field-by-field cleanup.
- Added reset/detach workflows in Allocation Controls plus a remediation board showing aligned vs drifted seeded accounts.


## Evidence lifecycle automation
Attachment-backed execution evidence needs an explicit lifecycle workflow. We keep it operator-run and audit-safe: scans record posture, archive mode removes only the expired file, and the transaction row plus note trail remain intact. Scheduled automation should call the management command instead of silently mutating rows inside request/response traffic.


## Pack CB — Broker trade confirmation linking
- Decision: broker confirmation linking should attach to the existing `HoldingTransaction` stop-policy row instead of creating a separate execution-audit object first. The transaction already holds the stop-policy exception context, so linking broker artifacts there keeps the workflow compact and reviewable.
- Decision: the first version links to existing broker artifacts already in the app: `ImportedBrokerSnapshot`, `BrokerPositionImportRun`, and `BrokerPositionImportResolution`. This keeps the feature useful immediately without waiting for a separate broker-order ingestion model.
- Decision: linking stays explicit and audit-safe. Mike chooses the broker artifacts from the stop-policy queue; the app never auto-links broker evidence from a plain page view.


## Pack CC — Ops command-center view
- Decision: the next ops step is a dedicated command-center page, not more dashboard sprawl. The dashboard stays broad; the command center becomes the operator's focused daily exception surface.
- Decision: the first version should aggregate existing trusted summaries — delivery health, stop-policy follow-up, evidence lifecycle, broker drift, and broker reconciliation runs — instead of inventing a new storage model first.
- Decision: lifecycle actions can be triggered directly from the command center because they already exist as explicit, audit-safe operator workflows. We reuse those actions rather than adding a second automation path.
- Added `ops_command_center`, navbar/allocation links, and command-center quick actions for evidence lifecycle runs.


## 2026-03-17 — Portfolio health score is a weighted triage layer, not a trading model
- The new portfolio health score is intentionally an operator-trust surface, not a predictive return score.
- It aggregates existing risk posture, drawdown monitoring, stop guardrails, sell/review queue pressure, and broker reconciliation debt into one weighted number so Mike can decide where to review first.
- We kept this scoring layer derived-only: no new database table and no backfilled state. The score is computed from the current portfolio posture each request so there is no silent mutation risk.
- The score is allowed to be opinionated and explainable instead of mathematically "perfect." The important design choice is that every penalty maps back to an existing visible workflow already present in the app.


## 2026-03-17 — Portfolio health should be checkpointed, not just recalculated
- Decision: the health score page should support persistent snapshots because a request-time score alone does not tell Mike whether posture is improving or deteriorating.
- Added `PortfolioHealthSnapshot` plus a CLI command and in-app save actions rather than auto-scheduling snapshots immediately.
- This keeps the first version explicit and operator-controlled while still creating an audit trail for future automation.
