#!/usr/bin/env python3
"""
Wonderland — weekly digest generator.

Reads news.json and podcasts.json (produced by aggregator.py / podcasts.py)
and distils the last 7 days into:

  digest.json  — machine-readable, for the site or any mailer to consume
  digest.md    — a ready-to-paste Beehiiv draft ("The Wonderland Brief")

Selection is deliberately simple and deterministic:
  • window: last 7 days (UTC)
  • headlines: newest first, max 2 per source, summaries preferred, top 10
  • episodes: newest first, max 2 per show, summaries preferred, top 5

Run weekly via .github/workflows/digest.yml, or on demand.
"""

import json, os, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(HERE, "news.json")
PODS = os.path.join(HERE, "podcasts.json")
OUT_JSON = os.path.join(HERE, "digest.json")
OUT_MD = os.path.join(HERE, "digest.md")

WINDOW_DAYS = 7
MAX_HEADLINES = 10
MAX_EPISODES = 5
MAX_PER_SOURCE = 2

now = dt.datetime.now(dt.timezone.utc)
cutoff = now - dt.timedelta(days=WINDOW_DAYS)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def pick(items, limit):
    """Newest first, cap per source, prefer items that carry a summary."""
    recent = []
    for it in items:
        ts = parse_ts(it.get("published", ""))
        if ts and ts >= cutoff:
            recent.append((ts, it))
    # summaries first within the window, then recency
    recent.sort(key=lambda p: (bool(p[1].get("summary")), p[0]), reverse=True)
    out, per_source = [], {}
    for ts, it in recent:
        src = it.get("source", "")
        if per_source.get(src, 0) >= MAX_PER_SOURCE:
            continue
        per_source[src] = per_source.get(src, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    # present newest-first
    out.sort(key=lambda it: it.get("published", ""), reverse=True)
    return out


def fmt_date(s):
    ts = parse_ts(s)
    return ts.strftime("%b %-d") if ts else ""


def main():
    news = load(NEWS).get("items", [])
    pods = load(PODS).get("items", [])

    headlines = pick(news, MAX_HEADLINES)
    episodes = pick(pods, MAX_EPISODES)

    week_of = (now - dt.timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    payload = {
        "generated": now.isoformat(),
        "week_of": week_of,
        "window_days": WINDOW_DAYS,
        "headlines": [
            {k: it.get(k, "") for k in ("title", "url", "source", "topics", "summary", "published")}
            for it in headlines
        ],
        "episodes": [
            {k: it.get(k, "") for k in ("title", "url", "source", "host", "topics", "summary", "duration", "published")}
            for it in episodes
        ],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # ---- Beehiiv-ready markdown draft ----
    lines = []
    lines.append(f"# The Wonderland Brief — week of {week_of}")
    lines.append("")
    lines.append("*What moved in venture this week — headlines and conversations worth your attention, from the Wonderland wire.*")
    lines.append("")
    lines.append("## On the wire")
    lines.append("")
    for it in headlines:
        summary = (it.get("summary") or "").strip()
        tail = f" — {summary}" if summary else ""
        lines.append(f"- **[{it.get('title','')}]({it.get('url','')})** · {it.get('source','')} · {fmt_date(it.get('published',''))}{tail}")
    lines.append("")
    lines.append("## In the listening room")
    lines.append("")
    for it in episodes:
        summary = (it.get("summary") or "").strip()
        dur = f" · {it['duration']}" if it.get("duration") else ""
        tail = f" — {summary}" if summary else ""
        lines.append(f"- **[{it.get('title','')}]({it.get('url','')})** · {it.get('source','')}{dur}{tail}")
    lines.append("")
    lines.append("---")
    lines.append("*Assembled from the [Wonderland wire](https://wonderland-venture-intelligence.vercel.app/track.html) and [Airwaves](https://wonderland-venture-intelligence.vercel.app/listen.html). For information only — not investment advice.*")
    lines.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"digest: {len(headlines)} headlines, {len(episodes)} episodes -> digest.json / digest.md")


if __name__ == "__main__":
    main()
