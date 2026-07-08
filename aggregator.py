#!/usr/bin/env python3
"""
Wonderland Wire — news aggregator agent.

Each run it:
  1. Collects headlines from the RSS feeds in feeds.yaml
  2. Dedupes against everything it has already processed (_seen.json)
  3. Sends ONLY new headlines to Claude (Sonnet) for a category + an
     original one-line summary (never the publisher's own words)
  4. Ages out anything older than RETENTION_DAYS and caps the list
  5. Writes news.json in exactly the shape the Track page reads:
        { "updated": ISO8601, "items": [
            { title, source, url, region, kind, topics, category,
              summary, published } ] }
     kind is "press" (news outlets) or "gp" (writing published by a VC firm).

No publisher text is stored — only links back and our own summaries.
"""

import os, re, json, sys, html, datetime as dt
from urllib.request import Request, urlopen
from urllib.parse import quote
from urllib.parse import urlparse, urlunparse

import yaml
import feedparser

try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

# ---- config ----
MODEL          = "claude-sonnet-5"
RETENTION_DAYS = 120        # keep a deeper archive so every source can reach its floor
MAX_ITEMS      = 1000        # total items kept in news.json; older ones roll off
GP_RETENTION_DAYS = 90       # GP-desk blogs post infrequently — keep their notes longer
GP_RESERVE     = 80          # (legacy) superseded by PER_SOURCE_FLOOR, which protects every source
MAX_PER_FEED   = 25          # newest N per feed each run (deeper backfill for the floor)
PER_SOURCE_FLOOR = 10        # every source keeps at least its newest N, even at the cap
BATCH          = 12          # headlines per Claude call
SEEN_CAP       = 800

# Some corporate VC blogs 403 the default feedparser user-agent. Present as a browser.
BROWSER_UA     = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}
# Server-side fetch proxies, tried when a feed returns nothing direct (bot walls / 404-to-bots).
PROXIES = [
    lambda u: "https://api.allorigins.win/raw?url=" + quote(u, safe=""),
    lambda u: "https://corsproxy.io/?url=" + quote(u, safe=""),
]

def _fetch_via_proxy(url):
    for build in PROXIES:
        try:
            req = Request(build(url), headers={"User-Agent": BROWSER_UA, **BROWSER_HEADERS})
            with urlopen(req, timeout=8) as r:
                data = r.read()
            if data and len(data) > 200:
                p = feedparser.parse(data)
                if p.entries:
                    return p
        except Exception:
            continue
    return None
# Topics taxonomy — an article may carry 1–3 of these. Keep in sync with track.html.
TOPICS         = ["Funding", "Funds & LPs", "Exits & M&A", "People",
                  "Policy", "Events", "Market & Data", "AI & Deep Tech", "Opinion"]

HERE     = os.path.dirname(os.path.abspath(__file__))
NEWS     = os.path.join(HERE, "news.json")
SEEN     = os.path.join(HERE, "_seen.json")
STATUS   = os.path.join(HERE, "_feedstatus.json")
FEED_STATUS = []
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
                        "region": feed.get("region", "Global"),
                        "kind": feed.get("kind", "press"),
                        "url": feed["url"]})
    return out

# ---- collect ----
def collect(feeds, seen):
    fresh = []
    FEED_STATUS.clear()
    for f in feeds:
        rec = {"name": f.get("name"), "kind": f.get("kind", "press"), "url": f["url"],
               "status": None, "bozo": 0, "error": None, "entries": 0, "new": 0}
        try:
            parsed = feedparser.parse(f["url"], agent=BROWSER_UA, request_headers=BROWSER_HEADERS)
            rec["status"] = getattr(parsed, "status", None)
            rec["bozo"] = int(getattr(parsed, "bozo", 0) or 0)
            if getattr(parsed, "bozo_exception", None):
                rec["error"] = str(parsed.bozo_exception)[:180]
            rec["entries"] = len(parsed.entries)
            rec["via"] = "direct"
            # Only a real bot-wall (403/429/451) is worth a proxy retry; a 404 is a wrong URL.
            if not parsed.entries and rec["status"] in (403, 429, 451):
                proxied = _fetch_via_proxy(f["url"])
                if proxied is not None and proxied.entries:
                    parsed = proxied
                    rec["entries"] = len(parsed.entries)
                    rec["via"] = "proxy"
                    rec["error"] = None
        except Exception as e:
            rec["error"] = f"EXC {e}"[:180]
            FEED_STATUS.append(rec)
            print(f"  ! {f['name']}: {e}", file=sys.stderr)
            continue
        n_new = 0
        for e in parsed.entries[:MAX_PER_FEED]:
            link = e.get("link", "")
            key = canon(link)
            if not key or key in seen:
                continue
            seen.add(key)
            n_new += 1
            fresh.append({
                "title": clean_title(e.get("title", "")),
                "url": link,
                "source": f["name"],
                "region": f.get("region", "Global"),
                "kind": f.get("kind", "press"),
                "published": parse_date(e).isoformat(),
            })
        rec["new"] = n_new
        FEED_STATUS.append(rec)
    return fresh

# ---- classify with Claude ----
def classify(client, batch):
    payload = [{"i": i, "title": it["title"], "source": it["source"]}
               for i, it in enumerate(batch)]
    prompt = (
        "You are a venture-capital news desk editor writing brief for LPs and GPs. "
        "For each headline below, return a JSON array. Each element must be: "
        "{\"i\": <index>, \"topics\": [1-3 topics], \"summary\": <2-3 sentence summary>}.\n\n"
        f"Choose topics ONLY from this exact list: {TOPICS}. "
        "Assign every topic that genuinely applies (most headlines fit 1-2; use up to 3). "
        "Topic guide: Funding = startup financing rounds/raises; Funds & LPs = fund closes, "
        "new funds, LP commitments; Exits & M&A = IPOs, acquisitions, secondaries; "
        "People = hires, departures, promotions, profiles; Policy = regulation, law, "
        "government, tax; Events = conferences, demo days, summits; Market & Data = trends, "
        "reports, benchmarks, macro; AI & Deep Tech = AI, ML, frontier/hard tech; "
        "Opinion = essays, analysis, commentary.\n\n"
        "For \"summary\": write 2-3 complete sentences (about 45-70 words) in your OWN words "
        "explaining what happened and why it matters to an LP or GP. Do NOT copy or lightly "
        "reword the headline or any publisher text — write original analysis. "
        "Return ONLY the JSON array, no prose.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=3200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        arr = json.loads(text)
        by_i = {int(x["i"]): x for x in arr if "i" in x}
        out = []
        for i, it in enumerate(batch):
            tag = by_i.get(i, {})
            topics = [t for t in (tag.get("topics") or []) if t in TOPICS]
            if not topics:
                topics = ["Market & Data"]
            it["topics"] = topics[:3]
            it["category"] = it["topics"][0]   # legacy single-tag, kept for safety
            it["summary"] = (tag.get("summary") or "").strip()
            out.append(it)
        return out
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        for it in batch:
            it["topics"] = ["Market & Data"]
            it["category"] = "Market & Data"
            it["summary"] = ""
        return batch

# ---- main ----
def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    feeds = load_feeds()
    print(f"Feeds: {len(feeds)}")

    store = load_json(NEWS, {"items": []})
    existing = store.get("items", [])
    # Backfill: legacy items from GP sources may lack kind="gp" (predate the field).
    gp_names = {f["name"] for f in feeds if f.get("kind") == "gp"}
    for it in existing:
        if it.get("source") in gp_names and it.get("kind") != "gp":
            it["kind"] = "gp"
    seen = set(load_json(SEEN, []))
    seen.update(canon(it["url"]) for it in existing)

    fresh = collect(feeds, seen)
    save_json(STATUS, FEED_STATUS)
    print(f"New headlines: {len(fresh)}")

    classified = []
    if fresh:
        if not key or Anthropic is None:
            print("  (no ANTHROPIC_API_KEY — keeping titles, no AI summaries)", file=sys.stderr)
            for it in fresh:
                it["topics"], it["category"], it["summary"] = ["Market & Data"], "Market & Data", ""
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

    def fresh_enough(it):
        days = GP_RETENTION_DAYS if it.get("kind") == "gp" else RETENTION_DAYS
        cutoff = now - dt.timedelta(days=days)
        try:
            return dt.datetime.fromisoformat(it["published"]) >= cutoff
        except Exception:
            return True
    merged = [it for it in merged if fresh_enough(it)]
    merged.sort(key=lambda it: it.get("published", ""), reverse=True)

    # ---- Cap at MAX_ITEMS while guaranteeing a per-source floor. ----
    # Step 1: reserve each source's newest PER_SOURCE_FLOOR items so a high-volume
    #         source can never evict a quieter one entirely.
    # Step 2: fill the remaining capacity with the newest items overall.
    floor_keep, floor_keys = [], set()
    per_source = {}
    for it in merged:                      # merged is newest-first
        src = it.get("source", "?")
        if per_source.get(src, 0) < PER_SOURCE_FLOOR:
            per_source[src] = per_source.get(src, 0) + 1
            floor_keep.append(it)
            floor_keys.add(canon(it["url"]))

    if len(floor_keep) >= MAX_ITEMS:
        # Floors alone exceed the cap (many sources): keep the newest floor items.
        merged = sorted(floor_keep, key=lambda it: it.get("published", ""), reverse=True)[:MAX_ITEMS]
    else:
        remaining = [it for it in merged if canon(it["url"]) not in floor_keys]
        fill = remaining[:MAX_ITEMS - len(floor_keep)]
        merged = sorted(floor_keep + fill, key=lambda it: it.get("published", ""), reverse=True)

    save_json(NEWS, {"updated": now.isoformat(), "items": merged})
    save_json(SEEN, list(seen)[-SEEN_CAP:])
    print(f"Wrote news.json — {len(merged)} items.")

if __name__ == "__main__":
    main()
