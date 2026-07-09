WONDERLAND WIRE — NEWS + PODCAST AGENT
A self-updating feed for the Articles and Airwaves pages. No command line.

────────────────────────────────────────────────────────
HOW IT WORKS  (you never write news)
────────────────────────────────────────────────────────
  feeds.yaml ─────────▶ aggregator.py ─┐
                                       ├─▶ Claude ──▶ news.json ─────▶ Articles page
  podcast_feeds.yaml ─▶ podcasts.py ───┘  (sorts +     podcasts.json ─▶ Airwaves page
  (your sources)        (the agents,       summarises)  (the output)    (reads them)
                         run on a timer)

  GitHub Actions runs both agents every 3 hours on GitHub's servers — free,
  nothing running on your computer. They read the RSS sources, ask Claude to
  sort and summarise anything new (in its own words — never the publisher's),
  and commit news.json and podcasts.json back to this repo. The live site
  reads those files directly. Done.

────────────────────────────────────────────────────────
BEFORE YOU START — two accounts
────────────────────────────────────────────────────────
  1) A GitHub account            → github.com  (free)
  2) An Anthropic API key        → console.anthropic.com → "API keys" → Create
     This is SEPARATE from your Claude.ai subscription. The agents only pay to
     read NEW headlines and episodes, so the running cost is small. Add a
     credit balance in the console's Billing tab.

────────────────────────────────────────────────────────
STEP 1 · Create the repository  (web)
────────────────────────────────────────────────────────
  • On github.com click the "+" (top right) → "New repository".
  • Name it e.g.  wonderland-wire  → set it to Public → "Create repository".

────────────────────────────────────────────────────────
STEP 2 · Upload the agent files  (web — drag & drop)
────────────────────────────────────────────────────────
  • On the new repo's page click "uploading an existing file".
  • Drag in all of the agent files:
        aggregator.py   podcasts.py   feeds.yaml   podcast_feeds.yaml
        requirements.txt   news.json   podcasts.json
        the .github folder  (it holds the schedule — keep the folder as-is)
    Tip: drag the .github FOLDER itself so the path .github/workflows/aggregate.yml
    is preserved. GitHub keeps folder structure when you drag a folder in.
  • Click "Commit changes".

────────────────────────────────────────────────────────
STEP 3 · Add your API key as a secret  (web)
────────────────────────────────────────────────────────
  • In the repo: Settings → Secrets and variables → Actions →
    "New repository secret".
  • Name:  ANTHROPIC_API_KEY
  • Secret: paste your key from console.anthropic.com → "Add secret".
  (Stored encrypted. The agents read it only while running on GitHub.)

────────────────────────────────────────────────────────
STEP 4 · Run it once  (web)
────────────────────────────────────────────────────────
  • Open the "Actions" tab. If prompted, click to enable workflows.
  • Pick "Wonderland Wire" → "Run workflow" → "Run workflow".
  • Wait a couple of minutes, refresh. A green check means it worked, and
    news.json and podcasts.json now hold real, summarised entries. From now on
    it reruns itself every 3 hours automatically.

────────────────────────────────────────────────────────
STEP 5 · Point the site at the feeds  (one-time)
────────────────────────────────────────────────────────
  • In the repo, click news.json → the "Raw" button → copy the URL from the
    address bar. It looks like:
        https://raw.githubusercontent.com/YOURNAME/wonderland-wire/main/news.json
  • In the site repo, open track.html and set, near the top of the <script>:
        const NEWS_URL = "https://raw.githubusercontent.com/YOURNAME/wonderland-wire/main/news.json";
  • Do the same for podcasts.json in listen.html:
        const PODCAST_URL = "https://raw.githubusercontent.com/YOURNAME/wonderland-wire/main/podcasts.json";
  • Commit. The site is deployed on Vercel with GitHub auto-deploy, so the
    commit publishes itself.

  That's the last manual step ever. The agents update this repo every 3 hours
  and the live pages read the fresh files automatically — no redeploys.

────────────────────────────────────────────────────────
TUNING
────────────────────────────────────────────────────────
  • News sources:    edit feeds.yaml (add any RSS URL), commit. Picked up next run.
  • Podcast sources: edit podcast_feeds.yaml (name, host, category, RSS URL).
  • Cadence:         edit the cron line in .github/workflows/aggregate.yml
                     ("0 */3 * * *" = every 3 hours; "0 */1 * * *" = hourly).
                     GitHub's scheduler is best-effort and may skip slots under
                     load; use "Run workflow" to force a run.
  • Retention/size:  RETENTION_DAYS and MAX_ITEMS at the top of each agent.
  • Diagnostics:     _feedstatus.json and _podstatus.json record, per feed, how
                     many entries were fetched and any error. Check these first
                     when a source goes quiet.

────────────────────────────────────────────────────────
GOOD TO KNOW
────────────────────────────────────────────────────────
  • Copyright-safe: it stores only links + its OWN summaries, never publisher text.
  • If the API key is missing or out of credit, it still runs and keeps headlines —
    it just skips the AI summaries.
  • raw.githubusercontent.com serves the JSON with permissive CORS headers, so the
    site reads the files directly from this repo. The site repo and this repo stay
    independent; neither needs to know about the other's deploy.
  • Some publishers block default feed-reader user-agents. The agents send a
    browser user-agent and fall back to a proxy on a genuine 403.
