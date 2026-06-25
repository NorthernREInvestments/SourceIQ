"""Reddit scraper via old.reddit.com JSON search."""

import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests

from market_spy.config import REDDIT_SUBREDDITS

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64",
    "Accept": "application/json",
}
REDDIT_CUTOFF_DAYS = 90
SUBREDDIT_DELAY_SECONDS = 3

_session = None


def _reddit_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(REDDIT_HEADERS)
        try:
            _session.get("https://old.reddit.com/", timeout=20)
        except Exception:
            pass
        time.sleep(SUBREDDIT_DELAY_SECONDS)
    return _session


def _search_subreddit(subreddit, niche, limit=25):
    url = f"https://old.reddit.com/r/{subreddit}/search.json"
    params = {
        "q": niche,
        "sort": "new",
        "t": "month",
        "limit": min(limit, 25),
        "restrict_sr": "on",
    }
    try:
        response = _reddit_session().get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    posts = []
    for child in payload.get("data", {}).get("children", []):
        if child.get("kind") != "t3":
            continue
        post = child.get("data", {})
        created = post.get("created_utc")
        if not created:
            continue
        post_date = datetime.utcfromtimestamp(int(created))
        permalink = post.get("permalink") or ""
        link = f"https://www.reddit.com{permalink}" if permalink else post.get("url")
        posts.append({
            "title": (post.get("title") or "").strip(),
            "url": link,
            "date": post_date,
            "score": int(post.get("score") or 0),
            "comments": int(post.get("num_comments") or 0),
        })
    return posts


def scrape_reddit(niche, limit_per_sub=10):
    """Search configured subreddits; keep posts from the last 90 days."""
    results = []
    cutoff = datetime.utcnow() - timedelta(days=REDDIT_CUTOFF_DAYS)
    seen_urls = set()

    for index, subreddit in enumerate(REDDIT_SUBREDDITS):
        if index > 0:
            time.sleep(SUBREDDIT_DELAY_SECONDS)
        posts = _search_subreddit(subreddit, niche, limit=25)
        sub_count = 0
        for post in posts:
            post_date = post["date"]
            if post_date < cutoff:
                continue
            link = post.get("url")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            score = post["score"]
            comments = post["comments"]
            results.append({
                "source": "Reddit",
                "side": "selling",
                "subreddit": subreddit,
                "name": post["title"],
                "url": link,
                "date": post_date,
                "score": score,
                "comments": comments,
                "engagement": score + comments,
            })
            sub_count += 1
            if sub_count >= limit_per_sub:
                break

    return results
