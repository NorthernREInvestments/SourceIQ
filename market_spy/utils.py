"""Parsing and HTTP utility helpers."""

import random
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from market_spy.config import HEADERS, REQUEST_DELAY


def sleep_random():
    time.sleep(random.uniform(*REQUEST_DELAY))


def safe_get(url, params=None, timeout=12):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def extract_price(text):
    if not text:
        return None
    text = str(text)
    m = re.search(r"\$\s?([0-9]{1,6}(?:\.[0-9]{1,2})?)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"([0-9]{1,6}(?:\.[0-9]{1,2})?)\s?(USD|usd)", text)
    if m:
        return float(m.group(1))
    return None


def parse_date_from_text(text):
    if not text:
        return None
    text = text.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        pass
    patterns = [r"(\d{4}-\d{2}-\d{2})", r"(\w+ \d{1,2}, \d{4})", r"(\d{1,2} \w+ \d{4})"]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return pd.to_datetime(m.group(1)).to_pydatetime()
            except Exception:
                pass
    m = re.search(r"(\d+)\s+day", text)
    if m:
        return datetime.utcnow() - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s+hour", text)
    if m:
        return datetime.utcnow() - timedelta(hours=int(m.group(1)))
    if "yesterday" in text.lower():
        return datetime.utcnow() - timedelta(days=1)
    return None


def age_label(dt):
    if not dt:
        return ("UNDATED", "gray")
    days = (datetime.utcnow() - dt).days
    if days <= 30:
        return ("FRESH", "green")
    if days <= 90:
        return ("RECENT", "yellow")
    if days <= 365:
        return ("AGING", "orange")
    return ("STALE — do not rely", "red")


def parse_int(s):
    try:
        if s is None:
            return 0
        return int(re.sub(r"[^0-9]", "", str(s)) or 0)
    except Exception:
        return 0


def safe_niche_slug(niche):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", niche.lower())
