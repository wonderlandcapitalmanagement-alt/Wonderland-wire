#!/usr/bin/env python3
"""
Wonderland Wire — news aggregator agent.

Each run it:
  1. Collects headlines from the RSS feeds in feeds.yaml
  2. Dedupes against everything it has already processed (_seen.json)
  3. Sends ONLY new headlines to Claude (Haiku) for a category + an
     original one-line summary (never the publisher's own words)
  4. Ages out anything older than RETENTION_DAYS and caps the list
  5. Writes news.json in exactly the shape the Track page reads:
        { "updated": ISO8601, "items": [
            { title, source, url, category, summary, published } ] }

No publisher text is stored — only links back and our own summaries.
"""

import os, re, json, sys, html, datetime as dt
from urllib.parse import urlparse, urlunparse

import yaml
import feedparser

try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

# ---- config ----
MODEL          = "claude-haiku-4-5-20251001"
RETENTION_DAYS = 14
MAX_ITEMS      = 120         # total items kept in news.json
MAX_PER_FEED   = 15          # newest N entries considered per feed each run
BATCH          = 12          # headlines per Claude call
SEEN_CAP       = 800
CATEGORIES     = ["Funds", "Deals", "Exits", "People", "Market"]

HERE     = os.path.dirname(os.path.abspath(__file__))
NEWS     = os.path.join(HERE, "news.json")
SEEN     = os.path.join(HERE, "_seen.json")
FEEDS    = os.path.join(HERE, "feeds.yaml")

now = dt.datetime.now(dt.timezone.utc)

# ---- helpers ----
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def canon(url):
    """Strip tracking params + fragments so the same story dedupes cleanly."""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", "")).lower()
    except Exception:
        return (url or "").strip().lower()

def parse_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return now

def clean_title(t):
    return html.unescape(re.sub(r"\s+", " ", (t or "")).strip())

def load_feeds():
    with open(FEEDS, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for feed in data.get("feeds", []):
        if isinstance(feed, str):
            out.append({"name": urlparse(feed).netloc, "url": feed})
        elif isinstance(feed, dict) and feed.get("url"):
            out.append({"name": feed.get("name") or urlparse(feed["url"]).netloc,
                        "url": feed["url"]})
    return out

# ---- collect ----
def collect(feeds, seen):
    fresh = []
    for f in feeds:
        try:
            parsed = feedparser.parse(f["url"])
        except Exception as e:
            print(f"  ! {f['name']}: {e}", file=sys.stderr)
            continue
        for e in parsed.entries[:MAX_PER_FEED]:
            link = e.get("link", "")
            key = canon(link)
            if not key or key in seen:
                continue
            seen.add(key)
            fresh.append({
                "title": clean_title(e.get("title", "")),
                "url": link,
                "source": f["name"],
                "published": parse_date(e).isoformat(),
            })
    return fresh

# ---- classify with Claude ----
def classify(client, batch):
    payload = [{"i": i, "title": it["title"], "source": it["source"]}
               for i, it in enumerate(batch)]
    prompt = (
        "You are a venture-capital news desk editor. For each headline below, return a JSON "
        "array. Each element: {\"i\": <index>, \"category\": one of "
        f"{CATEGORIES}, \"summary\": a single original sentence (max 24 words) written in your "
        "own words describing why it matters to an LP or GP}. Do NOT copy the headline's wording; "
        "paraphrase. Category guide: Funds = fund closes/LP commitments; Deals = financing rounds/"
        "investments; Exits = IPOs/M&A/secondaries; People = hires/departures/promotions; "
        "Market = data, trends, policy, everything else. Return ONLY the JSON array, no prose.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        arr = json.loads(text)
        by_i = {int(x["i"]): x for x in arr if "i" in x}
        out = []
        for i, it in enumerate(batch):
            tag = by_i.get(i, {})
            cat = tag.get("category")
            it["category"] = cat if cat in CATEGORIES else "Market"
            it["summary"] = (tag.get("summary") or "").strip()
            out.append(it)
        return out
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        for it in batch:
            it["category"] = "Market"
            it["summary"] = ""
        return batch

# ---- main ----
def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    feeds = load_feeds()
    print(f"Feeds: {len(feeds)}")

    store = load_json(NEWS, {"items": []})
    existing = store.get("items", [])
    seen = set(load_json(SEEN, []))
    seen.update(canon(it["url"]) for it in existing)

    fresh = collect(feeds, seen)
    print(f"New headlines: {len(fresh)}")

    classified = []
    if fresh:
        if not key or Anthropic is None:
            print("  (no ANTHROPIC_API_KEY — keeping titles, no AI summaries)", file=sys.stderr)
            for it in fresh:
                it["category"], it["summary"] = "Market", ""
            classified = fresh
        else:
            client = Anthropic(api_key=key)
            for n in range(0, len(fresh), BATCH):
                classified += classify(client, fresh[n:n + BATCH])

    # merge, dedupe, sort, age out, cap
    merged, keys = [], set()
    for it in classified + existing:
        k = canon(it["url"])
        if k in keys:
            continue
        keys.add(k)
        merged.append(it)

    cutoff = now - dt.timedelta(days=RETENTION_DAYS)
    def fresh_enough(it):
        try:
            return dt.datetime.fromisoformat(it["published"]) >= cutoff
        except Exception:
            return True
    merged = [it for it in merged if fresh_enough(it)]
    merged.sort(key=lambda it: it.get("published", ""), reverse=True)
    merged = merged[:MAX_ITEMS]

    save_json(NEWS, {"updated": now.isoformat(), "items": merged})
    save_json(SEEN, list(seen)[-SEEN_CAP:])
    print(f"Wrote news.json — {len(merged)} items.")

if __name__ == "__main__":
    main()
