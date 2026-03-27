# Roadmap

## Completed foundation
- Skeleton + apps + UI flow
- Universe seeding + ingestion
- Scanning + trade plans
- Alert hardening + Discord
- Scheduler + health checks
- Setup bootstrap + diagnostics
- Opportunity scoring + explainability
- Outcome tracking + review queue
- Manual Discord test alert
- Paper trading + position monitoring
- Ranking + alert queue visibility
- Market-aware scheduling + backfill
- Crypto provider auto-routing

## Completed — Automatic alert delivery baseline
- [x] Added channel-based delivery instead of Discord-only assumptions
- [x] Added optional email alert delivery using Django email backend
- [x] Added multi-channel `send_alerts` flow across enabled channels
- [x] Added multi-channel `send_test_alert` verification flow
- [x] Added dashboard visibility for enabled delivery channels
- [x] Added dashboard visibility for recent failed alert attempts

## Completed — Delivery health trust surface
- [x] Added dashboard drought detection for successful alert delivery
- [x] Added per-channel health summaries with recent sent/failed counts
- [x] Added repeated-failure streak visibility
- [x] Added `check_alert_delivery_health` command for operator trust checks
- [x] Added env-backed posture controls for delivery-health windows and thresholds

## Completed — Delivery-health escalation
- [x] Added persistent `OperatorNotification` records for operator-level notices
- [x] Added `escalate_delivery_health` command
- [x] Added scheduler wiring for automatic delivery-health escalation checks
- [x] Added dashboard visibility for recent escalation notices and cooldown posture

## Completed — Recovery notification loop
- [x] Added delivery-recovery notifications after an open incident clears
- [x] Added `notify_delivery_recovery` command
- [x] Added scheduler wiring for automatic delivery-recovery checks
- [x] Added dashboard visibility for last escalation, last recovery, and whether an incident is still open

## Completed — Paper-trade lifecycle usability
- [x] Added lifecycle stage to paper trades
- [x] Added active stop / active target fields
- [x] Added optional trailing-stop percentage
- [x] Added last/high/low seen prices for open paper trades
- [x] Added close reason tracking
- [x] Added `sync_trade_lifecycle` command
- [x] Added scheduler wiring for automatic paper-trade lifecycle sync
- [x] Added dashboard lifecycle summary and open-position management table
- [x] Added signal-detail management form for stop/target/trailing updates


## Pack BV — Copy / clone helpers for account retention overrides
- Allocation Controls now includes a dedicated clone form so Mike can copy one account's retention override onto another account without re-entering every window manually.
- Existing override rows now expose a **Clone** action that preselects the source account in the copy form.
- Copy flow supports safe replace behavior for an existing target override when Mike explicitly checks overwrite; otherwise it blocks accidental replacement.
- Done: account-override clone workflow, source prefill helper, docs refresh.
- Doing: making repeated retention setups faster across similar broker accounts without rewriting historical evidence rows.
- Left: optional account-family templates and any future storage-tier automation outside the app.

## What we are doing now
- [x] Adding copy/clone helpers so one account override can be reused across similar accounts.
- [x] Keeping clone actions forward-looking only so historical evidence rows are not silently rewritten.

## What is left
- [ ] Add account-family or preset templates if Mike wants one shared policy seed for multiple new accounts.
- [ ] Add background reporting for accounts whose current evidence mix no longer matches the configured override windows.


## Pack BW — Account-family retention templates
- Allocation Controls now includes reusable account-family retention templates so Mike can define one policy seed and apply it across several related account labels in one step.
- Added template create/edit/delete workflow plus a bulk apply form that accepts comma- or newline-separated account labels.
- Templates stay forward-looking: applying a template updates override rows for target accounts but does not rewrite historical evidence retention values.
- Done: template model, Allocation Controls UI, bulk apply workflow, docs refresh.
- Doing: making repeated family-level retention setup faster than one-off cloning.
- Left: optional policy recommendation helpers and any future storage-tier automation outside the app.

## What we are doing now
- [x] Adding reusable account-family retention templates.
- [x] Allowing one template to seed multiple account overrides in a single submit.

## What is left
- [ ] Add template recommendation helpers if Mike wants the app to suggest a family policy from existing override patterns.
- [ ] Add reporting for accounts whose live evidence mix drifts materially away from the applied template windows.


## Pack BX — Template recommendation helpers
- Allocation Controls now shows template recommendation helpers based on active account labels plus repeated override patterns already in use.
- Added direct **Prefill apply** links for uncovered accounts and family-template matches so Mike can seed overrides without retyping account labels.
- Added repeated-pattern recommendations that suggest saving a shared template when multiple account overrides already use the same retention windows.
- Done: recommendation summary service, Allocation Controls recommendation board, docs refresh.
- Doing: turning existing override patterns into faster reusable setup instead of relying only on manual template creation.
- Left: optional drift reporting between live account evidence mix and template intent.

## What we are doing now
- [x] Suggesting family-template applies from active account labels.
- [x] Surfacing repeated override patterns that are good candidates for template reuse.

## What is left
- [ ] Add drift reporting if Mike wants to compare active account evidence mix against the template windows currently assigned.
- [ ] Add any future storage-tier automation outside the app.


## Pack BY — Template drift reporting
- Per-account overrides can now carry source-template lineage so the app knows which template originally seeded each account policy.
- Allocation Controls and the dashboard now show which seeded accounts are aligned vs drifted, plus how many retention fields changed.
- Done: source-template field, drift summary service, dashboard/risk-settings visibility.
- Doing: making template usage auditable so Mike can see where account policies have wandered.
- Left: reset/detach remediation helpers so drift becomes actionable instead of just visible.

## What we are doing now
- [x] Showing seeded-account drift against the template that originally created the override.
- [x] Surfacing top drifted accounts on the dashboard and inside Allocation Controls.

## What is left
- [ ] Add direct reset-to-template and detach actions so drift can be resolved without field-by-field edits.
- [ ] Add any future storage-tier automation outside the app.


## Pack BZ — Drift remediation helpers
- Seeded account overrides can now be reset back to their template or intentionally detached from template tracking while keeping their current windows.
- Allocation Controls now includes a dedicated remediation board showing aligned vs drifted seeded accounts, changed fields, and simple reset/detach actions.
- Done: template-linked override reset, intentional detach workflow, docs refresh.
- Doing: turning drift reporting into an operator workflow instead of a passive report.
- Left: evidence lifecycle automation, broker-confirmation linking, ops dashboard, health scoring, and final hardening.

## What we are doing now
- [x] Turning seeded-template drift into direct remediation actions.
- [x] Keeping remediation audit-safe: reset rewrites only the current override row, detach preserves current windows and lineage history through docs/workflow.

## What is left
- [ ] Add evidence lifecycle automation for retention/cleanup follow-through.
- [ ] Add broker trade-confirmation linking and richer ops-level command center surfaces.
- [ ] Add final portfolio health scoring and hardening.


## Pack CA — Evidence lifecycle automation
- added durable lifecycle run tracking for attachment-backed execution evidence
- added operator-triggered scan / archive workflow from Stop-policy follow-up
- added management command `run_evidence_lifecycle` for scheduled scans or archive jobs
- dashboard + Allocation Controls now show lifecycle queue pressure, last run, and recommended next action

### Done
- lifecycle scans count expiring soon, expired, and missing-retention evidence rows
- archive mode clears expired attachments but preserves audit notes and row history

### Doing now
- tightening the operations layer so evidence retention work becomes a repeatable queue instead of manual spot checks

### Left
- broker trade confirmation linking
- ops command-center view
- portfolio health scoring
- final hardening / cleanup


## Pack CB — Broker trade confirmation linking
- waiting-for-confirmation stop-policy rows can now link directly to a broker snapshot, a broker reconciliation run, and/or a symbol-level broker resolution.
- Stop-policy follow-up now surfaces recent matching broker artifacts per row so Mike can tie the exception to real broker evidence instead of leaving it as a freeform note only.
- Done: broker-link fields on `HoldingTransaction`, queue-level link selectors, dashboard/Allocation Controls visibility, docs refresh.
- Doing: turning confirmation exceptions into explicit linked evidence instead of note-only placeholders.
- Left: ops command-center view, portfolio health scoring, and final hardening / cleanup.


## Pack CC — Ops command-center view
- Added a dedicated **Ops Command Center** page that rolls delivery trust, stop-policy queue pressure, evidence lifecycle posture, broker snapshot drift, and recent broker reconciliation runs into one operator surface.
- Added direct lifecycle scan / archive actions from the command-center page so Mike can run evidence queue work without leaving the ops surface.
- Done: command-center route, nav links, lifecycle quick actions, docs refresh.
- Doing: consolidating the daily operating picture into one page instead of splitting it across Dashboard, Allocation Controls, and Stop-policy follow-up.
- Left: portfolio health scoring, final hardening / cleanup.


## Pack CD — Portfolio health scoring
- Added a dedicated **Portfolio Health Score** surface under Allocation Controls so Mike can review one weighted health number before drilling into individual queues.
- The score rolls up account exposure posture, drawdown stress, stop-guardrail debt, sell/review queue pressure, and broker reconciliation debt.
- Dashboard and Ops Command Center now surface the same score so portfolio review starts with a single operator-grade triage view.
- Done: health scoring service, dedicated scorecard page, dashboard/ops visibility, docs refresh.
- Doing: turning several account-level posture tables into one first-pass score that tells Mike where to start.
- Left: final hardening / cleanup, broker import depth, and any future model-specific scoring refinements Mike wants.

## What we are doing now
- [x] Scoring account health from the posture layers already built into holdings, stops, drawdowns, and broker reconciliation.
- [x] Surfacing the weakest account first so the app acts like a triage console instead of a pile of separate reports.

## What is left
- [x] Add final hardening / cleanup across the ops stack.
- [ ] Refine score weights if Mike wants different emphasis for drawdown, stop debt, or reconciliation debt.


## 2026-03-17 Pack CE — Portfolio health snapshot history
- Added persistent `PortfolioHealthSnapshot` rows so portfolio-health scoring can be checkpointed over time.
- Added a manual **Save snapshot** action on both the Portfolio Health Score page and the Ops Command Center.
- Added `save_portfolio_health_snapshot` management command for repeatable operator snapshots from CLI or scheduler.
- Done: snapshot model, history summary, UI trend visibility, docs refresh.
- Done: automated deterioration notifications and scheduler wiring for unattended snapshot cadence.


## 2026-03-17 Pack CF — Automated portfolio health deterioration notifications
- Portfolio health snapshots are now saved and checked for deterioration automatically on every Nth scheduler cycle (default: every 4 cycles).
- Added `check_portfolio_health_deterioration` management command that compares the two most recent snapshots and fires an operator notification if the score has dropped by the configured threshold or the grade has moved into ACTION/CRITICAL territory.
- Added `PORTFOLIO_HEALTH` operator notification kind so health alerts are tracked separately from delivery-health escalations and are subject to their own cooldown window.
- Added `SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY`, `PORTFOLIO_HEALTH_DETERIORATION_THRESHOLD`, and `PORTFOLIO_HEALTH_ALERT_COOLDOWN_MINUTES` settings for full operator control.
- Also added missing `ALERT_RECOVERY_COOLDOWN_MINUTES` and `SCHEDULER_DELIVERY_RECOVERY_EVERY` settings that were referenced in code but absent from base.py.
- Done: deterioration service, command, scheduler wiring, settings, Discord/email notification support.
- Left: any future score-weight refinements or storage-tier automation outside the app.

## 2026-03-27 Pack CG — Signal decision support layer
- Added a reusable signal decision-support service that converts raw signal state into plain-language operator actions such as **Buy now**, **Watch closely**, **Review**, **Skip — risk cap**, and **Already held**.
- Dashboard top opportunities are now ranked with actionability in mind instead of score alone, and the homepage now shows a simple decision mix for the current top queue.
- Signals list now surfaces action labels plus next-step guidance so Mike can work from the page without opening every row first.
- Signal detail now shows an operator-action card and guardrail posture card so each setup explains what to do next and why.
- Added `docs/app_quality_review.md` to capture the broader product-quality review plus next recommended packs.
- Done: decision-support service, dashboard actionability surfacing, signal list/detail UX improvement, docs refresh.
- Left: recommendation feedback analytics, signal decay / freshness scoring, action-state filters, and deeper duplicate-entry protection by account.


## 2026-03-27 Pack CL — P&L color coding, data freshness age colors, signal detail improvements, HOW_TO_USE refresh

- Holdings performance page: P&L values on hero cards and all tables now color-coded green/red. Symbol columns link directly to holding detail. Realized % column also color-coded. Recent closed position dates use `M d H:i` format.
- Data freshness page: Age (min) column now color-coded — green <60, yellow-emphasis ≥120, red/bold for missing. Dates use `M d H:i` format. Applied to both the stale-symbols table and the crypto diagnostics table.
- Signal detail: Open-position banner promoted from small inline text to a full blue alert bar with a direct link to the holding. Action label badge enlarged (`fs-6`). Age badge extended: green "Fresh" badge for <1h, grey badge for 1–4h, amber text for 4–24h, yellow warning badge ≥24h.
- HOW_TO_USE.md: Added action filter docs, age-decay explanation, paper trade list page, Journal section, Analytics additions (decision outcomes + R-multiple distribution).
- Done: visual polish pass, docs refresh.


## 2026-03-27 Pack CK — Paper trade list page, morning brief paper trade summary

- Added a dedicated **Paper Trades** list page (`/signals/paper-trades/`) showing all open and closed simulated trades with symbol, direction, entry/exit prices, P&L, lifecycle stage, age, and stop/target levels. Accessible from the "More" dropdown in the navbar.
- Dashboard morning brief expanded from 3 to 4 columns: the new **Paper trades** tile shows open count and flags how many need attention (stop-risk or exit-ready lifecycle stages).
- Done: paper trade list view + template, morning brief expansion, navbar link, docs refresh.
- Left: signal freshness composite metric, score-weight tuning UI.


## 2026-03-27 Pack CJ — Watchlist signal display, symbol search signals, R-multiple analytics

- Watchlist "Recent signal" column now shows a formatted age badge (yellow ≥24h), color-coded score via `score_color_class`, and `M d H:i` date format — consistent with the rest of the app.
- Symbol search results now show the latest signal for each instrument inline: strategy, direction, timeframe, color-coded score, age, and total signal count. The "View signals" button shows the count.
- Analytics page adds an **R-multiple distribution** table showing how "Yes — took it" journal entries distribute across `<0R / 0–1R / 1–2R / 2–3R / 3R+` buckets with average R per bucket and an overall average.
- Done: watchlist signal display, symbol search enrichment, R-multiple analytics, docs refresh.
- Left: signal freshness as a composite metric, score-weight tuning UI.


## 2026-03-27 Pack CI — Server-side action filter, journal UX, account-scoped duplicate guard

- Signals list now supports a server-side `action` URL filter (`BUY_NOW`, `WATCH_CLOSE`, `REVIEW`, `SKIP_RISK`, `HOLDING`) so filtered queues can be bookmarked and paginate correctly. The action filter bar now navigates server-side instead of only filtering the current page client-side.
- Journal list overhauled: decision/outcome columns now color-coded (WIN=green badge, LOSS=red badge, YES=green, NO=red), win rate added to stats strip, filter panel shows active state + clear button, empty state distinguishes filter-active vs no entries.
- Duplicate holding warning is now account-scoped: same-account duplicates get a `warning`, cross-account duplicates get an informational `info` notice so multi-account setups are handled correctly.
- Done: server-side action filter, journal list UX, account-scoped duplicate guard, docs refresh.
- Left: score-weight refinements, signal freshness as a composite metric, account-family reporting.


## 2026-03-27 Pack CH — Signal staleness decay, duplicate holding protection, journal outcome analytics
- `assess_signal_action` now degrades stale signals automatically: BUY_NOW downgrades to WATCH_CLOSE after 48 h, WATCH_CLOSE downgrades to REVIEW after 72 h, with plain-language age-based reasons.
- Holding create now warns when an open position already exists for the same instrument, preventing silent duplicate exposure without blocking the operator.
- Analytics page now includes a **Journal decision outcomes** table showing WIN / LOSS / breakeven counts for YES (took it), NO (passed), and Skip decisions — closing the feedback loop between journal decisions and actual outcomes.
- Done: signal staleness decay, duplicate-holding guard, journal outcome analytics, docs refresh.
- Left: score-weight refinements, deeper account-scoped duplicate protection, advanced signal freshness as a scored service metric.

