"""Google Trends data fetcher."""

import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

from pytrends.request import TrendReq


def fetch_trends(niche):
    try:
        py = TrendReq(hl="en-US", tz=360)
        kw = [niche]
        py.build_payload(kw, timeframe="today 3-m")
        df = py.interest_over_time()
        if df is None or df.empty:
            return None
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        series = df[niche]
        res = []
        for idx, val in series.items():
            res.append((idx.strftime("%Y-%m-%d"), int(val)))
        return res
    except Exception:
        return None
