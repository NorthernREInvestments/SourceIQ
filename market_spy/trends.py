"""Google Trends data fetcher."""

import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

from pytrends.request import TrendReq

TREND_WINDOWS = (
    ("24h", "now 1-d"),
    ("7d", "now 7-d"),
    ("30d", "today 1-m"),
)

DIRECTION_LABELS = {
    "rising": "↑ Rising",
    "falling": "↓ Falling",
    "stable": "→ Stable",
}


def trends_direction(series):
    """Return (direction, change) from a (date, value) series."""
    if not series or len(series) < 2:
        return "stable", 0.0
    values = [float(v) for _, v in series]
    change = round(values[-1] - values[0], 1)
    if change > 2:
        return "rising", change
    if change < -2:
        return "falling", change
    return "stable", change


def format_trend_window(key: str, window: dict) -> str:
    """Format like: 24h ↑ Rising"""
    if not window.get("found"):
        return f"{key} —"
    label = DIRECTION_LABELS.get(window.get("direction", "stable"), "→ Stable")
    return f"{key} {label}"


def _fetch_series(niche: str, timeframe: str):
    try:
        py = TrendReq(hl="en-US", tz=360)
        py.build_payload([niche], timeframe=timeframe)
        df = py.interest_over_time()
        if df is None or df.empty:
            return None
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        if niche not in df.columns:
            return None
        series = df[niche]
        res = []
        for idx, val in series.items():
            res.append((idx.strftime("%Y-%m-%d"), int(val)))
        return res
    except Exception:
        return None


def fetch_trends(niche):
    """Long-range series for scoring and charts (90 days)."""
    return _fetch_series(niche, "today 3-m")


def interpret_trend_windows(windows: dict) -> str:
    """Plain-English summary when trend windows agree or conflict."""
    if not any(w.get("found") for w in windows.values()):
        return ""

    def direction(key: str) -> str | None:
        window = windows.get(key, {})
        if not window.get("found"):
            return None
        return window.get("direction", "stable")

    d24 = direction("24h")
    d7 = direction("7d")
    d30 = direction("30d")
    found = [d for d in (d24, d7, d30) if d is not None]
    if not found:
        return ""

    if len(found) == 3 and all(d == "rising" for d in found):
        return "Sustained demand across all timeframes — strong signal."

    if len(set(found)) == 1:
        return ""

    if d24 == "rising" and d30 == "falling":
        return "Short-term spike — sustained demand not confirmed. Research before committing."

    if d24 == "rising" and d30 not in (None, "rising"):
        return "Short-term spike only — wait for sustained trend before investing."

    if d24 == "falling" and d30 == "rising":
        return "Recent dip but 30-day trend still rising — watch before committing."

    if d30 == "falling" and d24 != "falling":
        return "Longer-term demand is falling — compare other options before investing."

    return "Mixed trend signals — verify margins and demand before ordering inventory."


def fetch_trends_windows(niche: str) -> dict:
    """Fetch 24h, 7d, and 30d trend directions from Google Trends."""
    windows = {}
    for key, timeframe in TREND_WINDOWS:
        series = _fetch_series(niche, timeframe)
        if series and len(series) >= 2:
            direction, change = trends_direction(series)
            windows[key] = {
                "found": True,
                "direction": direction,
                "change": change,
            }
        else:
            windows[key] = {
                "found": False,
                "direction": "stable",
                "change": 0,
            }
    return windows
