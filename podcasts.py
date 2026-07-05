#!/usr/bin/env python3
"""
Wonderland — podcast aggregator agent.

Same design as aggregator.py, but for podcast episodes:
  1. Collects the newest episodes from the shows in podcast_feeds.yaml
  2. Dedupes against everything already processed (_seen_pods.json)
  3. Sends ONLY new episodes to Claude (Haiku) for 1-3 topics + an
     original multi-sentence summary (never the publisher's own words)
  4. Ages out anything older than RETENTION_DAYS and caps the list
  5. Writes podcasts.json in the shape the Listen page reads:
        { "updated": ISO8601, "items": [
            { title, source, host, url, topics, summary, published, duration } ] }
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
MODEL          = "claude-sonnet-5"

BROWSER_UA     = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}
RETENTION_DAYS = 60          # podcasts publish less often than news
MAX_ITEMS      = 120
MAX_PER_FEED   = 6           # newest N episodes per show each run
BATCH          = 10
SEEN_CAP       = 800
# Keep this list identical to aggregator.py / track.html / listen.html
TOPICS         = ["Funding", "Funds & LPs", "Exits & M&A", "People",
                  "Policy", "Events", "Market & Data", "AI & Deep Tech", "Opinion"]

HERE   = os.path.dirname(os.path.abspath(__file__))
OUT    = os.path.join(HERE, "podcasts.json")
SEEN   = os.path.join(HERE, "_seen_pods.json")
FEEDS  = os.path.join(HERE, "podcast_feeds.yaml")

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

def fmt_duration(raw):
    """Return a 'NN min' label from itunes:duration (seconds or HH:MM:SS)."""
    if not raw:
        return ""
    raw = str(raw).strip()
    if raw.isdigit():
        m = round(int(raw) / 60)
        return f"{m} min" if m else ""
    parts = raw.split(":")
    try:
        parts = [int(p) for p in parts]
    except Exception:
        return ""
    if len(parts) == 3:
        mins = parts[0] * 60 + parts[1]
    elif len(parts) == 2:
        mins = parts[0]
    else:
        return ""
    return f"{mins} min" if mins else ""

def load_feeds():
    with open(FEEDS, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for feed in data.get("feeds", []):
        if isinstance(feed, dict) and feed.get("url"):
            out.append({"name": feed.get("name") or urlparse(feed["url"]).netloc,
                        "host": feed.get("host", ""),
                        "url": feed["url"]})
    return out

# ---- collect ----
def collect(feeds, seen):
    fresh = []
    status = []
    for f in feeds:
        entry_count = 0
        err = None
        try:
            # Fetch bytes ourselves (browser headers + timeout), then parse —
            # feedparser's own fetcher gets blocked by some hosts (e.g. Substack).
            from urllib.request import Request, urlopen
            req = Request(f["url"], headers={"User-Agent": BROWSER_UA, **BROWSER_HEADERS})
            with urlopen(req, timeout=15) as r:
                data = r.read()
            parsed = feedparser.parse(data)
            if not parsed.entries:  # fallback to feedparser-native fetch
                parsed = feedparser.parse(f["url"], agent=BROWSER_UA, request_headers=BROWSER_HEADERS)
            entry_count = len(parsed.entries)
        except Exception as e:
            err = str(e)[:120]
            print(f"  ! {f['name']}: {e}", file=sys.stderr)
            status.append({"name": f["name"], "entries": 0, "error": err})
            continue
        status.append({"name": f["name"], "entries": entry_count, "error": err})
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
                "host": f["host"],
                "duration": fmt_duration(e.get("itunes_duration")),
                "published": parse_date(e).isoformat(),
            })
    try:
        with open(os.path.join(HERE, "_podstatus.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh, indent=1)
    except Exception:
        pass
    return fresh

# ---- classify with Claude ----
def classify(client, batch):
    payload = [{"i": i, "title": it["title"], "show": it["source"]}
               for i, it in enumerate(batch)]
    prompt = (
        "You are a venture-capital podcast editor writing a brief for LPs and GPs. "
        "For each episode below, return a JSON array. Each element must be: "
        "{\"i\": <index>, \"topics\": [1-3 topics], \"summary\": <2-3 sentence summary>}.\n\n"
        f"Choose topics ONLY from this exact list: {TOPICS}. "
        "Assign every topic that genuinely applies (most fit 1-2; use up to 3). "
        "Topic guide: Funding = startup financing rounds; Funds & LPs = fund closes, LP "
        "commitments; Exits & M&A = IPOs, acquisitions; People = founder/investor profiles, "
        "hires, moves; Policy = regulation, government; Events = conferences, live shows; "
        "Market & Data = trends, macro, benchmarks; AI & Deep Tech = AI, ML, frontier tech; "
        "Opinion = interviews, essays, commentary.\n\n"
        "For \"summary\": write 2-3 complete sentences (about 45-70 words) in your OWN words "
        "describing what the episode likely covers and why it matters to an LP or GP. Do NOT "
        "copy or reword the episode title or any publisher text — write original framing. "
        "Return ONLY the JSON array, no prose.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=3000,
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
            it["topics"] = (topics or ["Opinion"])[:3]
            it["summary"] = (tag.get("summary") or "").strip()
            out.append(it)
        return out
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        for it in batch:
            it["topics"], it["summary"] = ["Opinion"], ""
        return batch

# ---- main ----
def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    feeds = load_feeds()
    print(f"Shows: {len(feeds)}")

    store = load_json(OUT, {"items": []})
    existing = store.get("items", [])
    seen = set(load_json(SEEN, []))
    seen.update(canon(it["url"]) for it in existing)

    fresh = collect(feeds, seen)
    print(f"New episodes: {len(fresh)}")

    classified = []
    if fresh:
        if not key or Anthropic is None:
            print("  (no ANTHROPIC_API_KEY — keeping titles, no AI summaries)", file=sys.stderr)
            for it in fresh:
                it["topics"], it["summary"] = ["Opinion"], ""
            classified = fresh
        else:
            client = Anthropic(api_key=key)
            for n in range(0, len(fresh), BATCH):
                classified += classify(client, fresh[n:n + BATCH])

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

    save_json(OUT, {"updated": now.isoformat(), "items": merged})
    save_json(SEEN, list(seen)[-SEEN_CAP:])
    print(f"Wrote podcasts.json — {len(merged)} items.")

if __name__ == "__main__":
    main()
