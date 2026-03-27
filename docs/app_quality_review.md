# App quality review — stock trader app

## What the app already does well
- Clear modular separation between market data, signals, strategies, risk, portfolios, journal, and dashboard surfaces.
- Strong operator tooling through commands, scheduler loops, health checks, and queue-style workflows.
- Good foundation for actionable trading support through signal scoring, trade plans, paper trading, and held-position monitoring.

## Highest-value recommendations

### 1) Turn raw signals into operator actions
The app already computes score, trade-plan data, and guardrail posture. The next quality step is to translate that into a plain-language action such as **Buy now**, **Watch closely**, **Review**, or **Skip — risk cap**.

Why this matters:
- reduces decision friction
- makes the dashboard more useful at a glance
- helps separate strong opportunities from raw data noise

### 2) Make the dashboard feel like a command center
The homepage should answer three questions quickly:
- What should I act on now?
- What should I review next?
- What is blocked by risk?

This is more useful than showing score alone.

### 3) Keep the app operator-friendly
Useful improvements going forward:
- add saved dashboard views for “Buy now” and “Risk-blocked” queues
- add decision-history analytics to compare recommendation quality over time
- add signal aging / decay so old signals lose urgency automatically
- add more explicit “why this is blocked” summaries for each candidate

## Updates implemented in this pack
- Added a reusable signal decision-support service that converts signal state into operator-facing actions.
- Added dashboard decision visibility so top opportunities are ranked not just by score, but by actionability.
- Added action badges and next-step guidance on the Signals list.
- Added a signal-detail decision card so each signal explains what the operator should do next and why.

## Updates implemented in Pack CH
- Signal staleness decay: `assess_signal_action` now degrades BUY_NOW → WATCH_CLOSE after 48 h, and WATCH_CLOSE → REVIEW after 72 h, with plain-language age-based reasons.
- Duplicate holding guard: holding create warns when an open position for the same instrument already exists.
- Journal decision outcomes: analytics page now cross-references journal YES/NO/Skip decisions with WIN/LOSS/breakeven outcomes.

## Updates implemented in Pack CI
- Server-side `action` filter on signals list — `BUY_NOW`, `WATCH_CLOSE`, `REVIEW`, `SKIP_RISK`, `HOLDING` are now URL params that paginate correctly and can be saved as presets.
- Journal list overhauled: color-coded decisions/outcomes, win rate stat, clear filters button, improved empty states.
- Duplicate holding guard is now account-scoped: same-account = warning, cross-account = info notice.

## Recommended next packs
1. **Signal freshness as a composite metric** — surface a freshness score alongside the signal score so aging setups show composite urgency beyond just a downgraded action label.
2. **Score-weight tuning UI** — allow adjusting the thresholds in `assess_signal_action` (currently 85/75/60) from a settings page rather than code.
3. **Journal R-multiple trend** — chart realized R-multiples over time on the analytics page to show whether risk/reward is improving.
