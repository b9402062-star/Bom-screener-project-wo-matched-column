# BOM Restricted Party Screener — auto-refreshing Company Review List

Screens BOM manufacturer/supplier names against four restricted-party lists:

| Source | What it is |
|---|---|
| **UFLPA Entity List** | DHS forced-labor entity list (Uyghur Forced Labor Prevention Act) |
| **FCC Covered List** | Banned telecom/surveillance equipment & services (47 CFR § 1.50002) |
| **EU Sanctioned Entities** | Full EU Financial Sanctions Files (asset-freeze/travel-ban list), organizations only |
| **OFAC** | Treasury's SDN, Sectoral Sanctions (SSI), Non-SDN CMIC, Non-SDN Menu-Based, and Capta lists |

The Company Review List **updates itself on a schedule with no one having to run anything by hand.** Once set up, GitHub's own infrastructure re-pulls each source weekly (or on demand) and republishes the refreshed data automatically.

## How it stays current, in one sentence

A GitHub Actions workflow runs `refresh_lists.py` on a cron schedule → the script re-fetches each source from its **official** origin (EU Financial Sanctions Files XML, trade.gov's Consolidated Screening List, and a live scrape of the DHS UFLPA page; FCC Covered List is a small maintained list since fcc.gov blocks automated fetches) → it writes the result to `data/company_list.json` → the workflow commits that file if it changed → GitHub Pages serves the updated file → the tool's next page load picks it up automatically via `fetch()`.

If any one source fails on a given run (network hiccup, site change, etc.), the script keeps that source's data from the previous successful run rather than blanking it out, and logs a warning to `data/refresh_log.json`. Nothing breaks; you just see a note next time you check.

## One-time setup (about 10 minutes)

1. **Create a GitHub repo** (public or private — Pages works either way on a paid plan; public repos get Pages free).
2. **Push these files** to the repo, preserving the folder structure:
   ```
   bom_screener.html
   classify.py
   refresh_lists.py
   data/company_list.json        (already populated — today's data, so it works immediately)
   data/refresh_log.json
   .github/workflows/refresh.yml
   ```
3. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from a branch" → branch `main`, folder `/ (root)` → Save.
4. **Enable Actions** (usually on by default for a new repo): Settings → Actions → General → allow workflows to run.
5. **Check the workflow has write permission**: Settings → Actions → General → Workflow permissions → "Read and write permissions" (needed so it can commit the refreshed data back to the repo).
6. Done. Share the Pages URL with your team, e.g.:
   ```
   https://<your-org-or-username>.github.io/<repo-name>/bom_screener.html
   ```

From here, nobody needs to touch it. The workflow fires every Monday at 06:00 UTC on its own; you can also trigger it early from the repo's **Actions** tab → "Refresh Company Review List" → "Run workflow" if a new sanctions action drops and you don't want to wait for the schedule.

## Important: this must be served over http(s)

The tool fetches `./data/company_list.json` at page load. That works when the page is served by GitHub Pages, an intranet server, `python -m http.server`, etc. — anything that serves it over `http://` or `https://`.

**It will not work if someone just double-clicks the HTML file and opens it from disk** (`file://...`). Browsers block that kind of local file-to-file fetch for security reasons. If your team needs an offline/no-server fallback, ask about baking a static snapshot back into the file — that's a different, non-auto-updating mode.

## Checking on it occasionally (optional, not required)

- **In the tool itself**: the "Company Review List" panel shows live counts per source and a "Last refresh" date. Hover over that line — if a source fell back to stale data on the last run, the tooltip will say so.
- **In GitHub**: the Actions tab shows a green check or red X per run, and each run uploads `refresh_log.json` as a downloadable artifact with the full detail.
- **In the repo history**: every successful data change is its own commit (`Automated refresh: YYYY-MM-DD`), so you can see exactly when each source last actually changed.

## Scope notes (things this tool can't do)

- **FCC categorical bans**: since 2025–2026 the FCC has also banned equipment *by country of origin/category* rather than by named company (e.g. all foreign-produced routers, UAS components, power inverters). A name-matching tool structurally can't screen for "made in X" rules — review those separately at the FCC Covered List URL.
- **EU sectoral/trade restrictions**: the EU Sanctioned Entities source covers the asset-freeze/travel-ban list, not blanket sectoral bans that apply by category (e.g. ownership-threshold-based restrictions on Russian state enterprises).
- Sources not included: BIS Entity/Denied Persons/Unverified/Military-End-User lists, DoW's Section 1260H "Chinese Military Companies" list, and NDAA Section 889/FAR-DFARS covered telecom — these were deliberately scoped out per current requirements (OFAC, FCC, UFLPA, EU only). They can be added back into `refresh_lists.py` later if needed; the earlier version of this script (with all of them included) is straightforward to restore from git history or by asking for it again.

## Files in this project

| File | Purpose |
|---|---|
| `bom_screener.html` | The screening tool. Fetches data at runtime — no data embedded in the file. |
| `refresh_lists.py` | The unattended refresh job. Can also be run manually: `pip install beautifulsoup4 && python refresh_lists.py` |
| `classify.py` | Heuristic used only for OFAC rows where the source data doesn't tag "type" (distinguishes companies from individuals; this tool intentionally screens organizations, not people) |
| `data/company_list.json` | The current data the tool loads. Rewritten by every refresh run. |
| `data/refresh_log.json` | Machine-readable report of the last run: what worked, what fell back, and why. |
| `.github/workflows/refresh.yml` | The GitHub Actions schedule + commit-back logic. |
