from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from django.conf import settings


PRESETS: dict[str, dict[str, str]] = {
    "conservative": {
        "OPERATOR_PRESET": "conservative",
        "ALERT_COOLDOWN_MINUTES": "60",
        "ALERT_MAX_PER_DAY": "6",
        "ALERT_MAX_SIGNAL_AGE_MINUTES": "2880",
        "ALERT_MIN_SCORE_EVENT": "75",
        "ALERT_MIN_SCORE_STATE": "65",
        "ALERT_STATE_CHANGE_ONLY": "true",
        "SCHEDULER_MAX_SYMBOLS_PER_CYCLE": "5",
        "SCHEDULER_THROTTLE_SECONDS": "12",
        "POSITION_DETERIORATION_ALERT_PCT": "1.5",
        "POSITION_STOP_ALERT_DISTANCE_PCT": "0.75",
        "POSITION_ALERT_COOLDOWN_MINUTES": "180",
    },
    "balanced": {
        "OPERATOR_PRESET": "balanced",
        "ALERT_COOLDOWN_MINUTES": "30",
        "ALERT_MAX_PER_DAY": "12",
        "ALERT_MAX_SIGNAL_AGE_MINUTES": "4320",
        "ALERT_MIN_SCORE_EVENT": "60",
        "ALERT_MIN_SCORE_STATE": "50",
        "ALERT_STATE_CHANGE_ONLY": "true",
        "SCHEDULER_MAX_SYMBOLS_PER_CYCLE": "10",
        "SCHEDULER_THROTTLE_SECONDS": "10",
        "POSITION_DETERIORATION_ALERT_PCT": "2.0",
        "POSITION_STOP_ALERT_DISTANCE_PCT": "1.0",
        "POSITION_ALERT_COOLDOWN_MINUTES": "120",
    },
    "aggressive": {
        "OPERATOR_PRESET": "aggressive",
        "ALERT_COOLDOWN_MINUTES": "15",
        "ALERT_MAX_PER_DAY": "25",
        "ALERT_MAX_SIGNAL_AGE_MINUTES": "4320",
        "ALERT_MIN_SCORE_EVENT": "45",
        "ALERT_MIN_SCORE_STATE": "35",
        "ALERT_STATE_CHANGE_ONLY": "false",
        "SCHEDULER_MAX_SYMBOLS_PER_CYCLE": "15",
        "SCHEDULER_THROTTLE_SECONDS": "8",
        "POSITION_DETERIORATION_ALERT_PCT": "2.5",
        "POSITION_STOP_ALERT_DISTANCE_PCT": "1.25",
        "POSITION_ALERT_COOLDOWN_MINUTES": "60",
    },
}


def env_file_path() -> Path:
    return Path(settings.BASE_DIR) / ".env"


def read_env_lines(path: Path | None = None) -> list[str]:
    path = path or env_file_path()
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()



def current_policy_snapshot() -> OrderedDict[str, str]:
    pairs = OrderedDict()
    for key in [
        "OPERATOR_PRESET",
        "ALERT_COOLDOWN_MINUTES",
        "ALERT_MAX_PER_DAY",
        "ALERT_MAX_SIGNAL_AGE_MINUTES",
        "ALERT_MIN_SCORE_EVENT",
        "ALERT_MIN_SCORE_STATE",
        "ALERT_STATE_CHANGE_ONLY",
        "EQUITY_ALERT_SESSION_START",
        "EQUITY_ALERT_SESSION_END",
        "SCHEDULER_MAX_SYMBOLS_PER_CYCLE",
        "SCHEDULER_THROTTLE_SECONDS",
        "POSITION_DETERIORATION_ALERT_PCT",
        "POSITION_STOP_ALERT_DISTANCE_PCT",
        "POSITION_ALERT_COOLDOWN_MINUTES",
    ]:
        value = getattr(settings, key, None)
        pairs[key] = "" if value is None else str(value)
    return pairs



def apply_preset_to_env(name: str, path: Path | None = None) -> tuple[Path, list[str]]:
    preset_key = (name or "").strip().lower()
    if preset_key not in PRESETS:
        raise KeyError(preset_key)

    path = path or env_file_path()
    lines = read_env_lines(path)
    values = PRESETS[preset_key]
    updated: list[str] = []
    seen: set[str] = set()
    output: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            output.append(raw_line)
            continue
        key, _old = raw_line.split("=", 1)
        key = key.strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            updated.append(key)
            seen.add(key)
        else:
            output.append(raw_line)

    if output and output[-1].strip() != "":
        output.append("")
    if "OPERATOR_PRESET" not in seen or any(key not in seen for key in values):
        if not any(line.strip() == "# Operator preset" for line in output):
            output.append("# Operator preset")
        for key, value in values.items():
            if key in seen:
                continue
            output.append(f"{key}={value}")
            updated.append(key)
            seen.add(key)

    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return path, updated
