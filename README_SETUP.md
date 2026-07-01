WONDERLAND WIRE — NEWS AGENT SETUP
A self-updating news feed for the Track page. No command line — just web buttons.

────────────────────────────────────────────────────────
HOW IT WORKS  (you never write news)
────────────────────────────────────────────────────────
  feeds.yaml ──▶ aggregator.py ──▶ Claude (Haiku) ──▶ news.json ──▶ Track page
  (your        (the agent,         (categorises +     (the output)   (reads it)
   sources)     runs on a timer)    summarises)

  GitHub Actions runs the agent every 3 hours on GitHub's servers — free,
  nothing running on your computer. It reads your RSS sources, asks Claude to
  sort and summarise anything new (in its own words — never the publisher's),
  and saves news.json. Your site reads that file. Done.

────────────────────────────────────────────────────────
BEFORE YOU START — two free/cheap accounts
────────────────────────────────────────────────────────
  1) A GitHub account            → github.com  (free)
  2) An Anthropic API key        → console.anthropic.com → "API keys" → Create
     This is SEPARATE from your Claude.ai subscription. On the Haiku model the
     agent costs roughly a few cents to ~$1 a month — it only pays to read NEW
     headlines. Add a small credit balance in the console's Billing tab.

────────────────────────────────────────────────────────
STEP 1 · Create the repository  (web)
────────────────────────────────────────────────────────
  • On github.com click the "+" (top right) → "New repository".
  • Name it e.g.  wonderland-wire  → set it to Public → "Create repository".

────────────────────────────────────────────────────────
STEP 2 · Upload the agent files  (web — drag & drop)
────────────────────────────────────────────────────────
  • On the new repo's page click "uploading an existing file".
  • Unzip wonderland-agent.zip, then drag ALL of its contents into the browser:
        aggregator.py   feeds.yaml   requirements.txt   news.json
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
  (Stored encrypted. The agent reads it only while running on GitHub.)

────────────────────────────────────────────────────────
STEP 4 · Run it once  (web)
────────────────────────────────────────────────────────
  • Open the "Actions" tab. If prompted, click to enable workflows.
  • Pick "Wonderland Wire" → "Run workflow" → "Run workflow".
  • Wait ~1 minute, refresh. A green check means it worked, and news.json in
    the repo now holds real, summarised stories. From now on it reruns itself
    every 3 hours automatically.

────────────────────────────────────────────────────────
STEP 5 · Point your site at the feed  (one-time)
────────────────────────────────────────────────────────
  • In the repo, click news.json → the "Raw" button → copy the URL in the
    address bar. It looks like:
        https://raw.githubusercontent.com/YOURNAME/wonderland-wire/main/news.json
  • Open track.html (in your wonderland-site folder) in a text editor.
    Near the top of the <script> find:
        const NEWS_URL = "";
    Paste your Raw URL between the quotes and save:
        const NEWS_URL = "https://raw.githubusercontent.com/YOURNAME/wonderland-wire/main/news.json";
  • Re-drag the wonderland-site folder into Netlify (Deploys tab) ONE more time.

  That's the last manual step ever. The agent updates GitHub every 3 hours and
  your live Track page reads the fresh file automatically — no more redeploys.

────────────────────────────────────────────────────────
TUNING
────────────────────────────────────────────────────────
  • Sources:   edit feeds.yaml (add any RSS URL), commit. Picked up next run.
  • Cadence:   edit the cron line in .github/workflows/aggregate.yml
               ("0 */3 * * *" = every 3 hours; "0 */1 * * *" = hourly).
  • Categories the agent sorts into: Funds · Deals · Exits · People · Market
               (these become the filter chips on the Track page automatically).
  • Retention/size: RETENTION_DAYS and MAX_ITEMS at the top of aggregator.py.

────────────────────────────────────────────────────────
GOOD TO KNOW
────────────────────────────────────────────────────────
  • Copyright-safe: it stores only links + its OWN summaries, never publisher text.
  • If the API key is missing or out of credit, it still runs and keeps headlines —
    it just skips the AI summaries that run.
  • raw.githubusercontent.com allows the site to read the file directly, so you
    don't need to connect the site repo to Netlify. Keep your simple drag-deploy.
