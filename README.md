# BOM Restricted Party Screener (Full) — 7-source auto-refreshing Company Review List

Screens BOM manufacturer/supplier names against **seven** restricted-party source categories (14 individual sub-lists):

| Category | Sub-lists | What it is |
|---|---|---|
| **UFLPA Entity List** | 1 | DHS forced-labor entity list (Uyghur Forced Labor Prevention Act) |
| **OFAC sanctions lists/programs** | SDN, Sectoral Sanctions (SSI), Non-SDN CMIC, Non-SDN Menu-Based, Palestinian Legislative Council, Capta | Treasury Department sanctions |
| **BIS restricted-party lists** | Entity List, Denied Persons List, Unverified List, Military End User (MEU) List | Commerce Dept./Bureau of Industry and Security export-control lists |
| **NDAA Sec. 889 / FAR-DFARS Covered Telecom** | 1 (5 named entities) | Covered telecom/surveillance equipment banned government-wide (FAR 52.204-25 / DFARS 252.204-7018) |
| **DoW Section 1260H List** | 1 | "Chinese military companies" list (NDAA FY2021 Sec. 1260H) |
| **FCC Covered List** | 1 | Banned telecom/surveillance equipment & services (47 CFR § 1.50002) |
| **EU Sanctioned Entities** | 1 | Full EU Financial Sanctions Files (asset-freeze/travel-ban list), organizations only |

This is a **separate tool from the 4-source version** (UFLPA/FCC/EU/OFAC only) — the two don't share a repo, data folder, or deployment. Deploy this one independently if you want full 7-category coverage.

The Company Review List **updates itself daily with no one having to run anything by hand** — same auto-refresh design as the 4-source tool, just with three more fetchers (BIS, NDAA 889, DoW 1260H) added.

## How it stays current

A GitHub Actions workflow runs `refresh_lists.py` daily → the script re-fetches each source from its **official** origin:
- **UFLPA**: live scrape of the DHS UFLPA Entity List page
- **OFAC + BIS**: trade.gov's Consolidated Screening List (one bulk CSV covering 10 of the 14 sub-lists)
- **EU**: the official EU Financial Sanctions Files XML
- **DoW Section 1260H**: stateful delta-tracking via the Federal Register API — polls for newer DoW notices and applies their added/removed entities on top of a persisted baseline (`data/dow_1260h_state.json`), never overwriting the baseline unless a new notice parses with confidence
- **FCC Covered List** and **NDAA Sec. 889**: maintained as small static lists in the script, since fcc.gov actively blocks automated fetches and both lists are small and rarely change (check them periodically at the source URLs in the script's comments)

→ writes the result to `data/company_list.json` → the workflow commits that file if it changed → GitHub Pages serves the updated file → the tool's next page load picks it up automatically via `fetch()`.

**Resilience**: if any one source fails on a given run, the script keeps that source's data from the previous successful run rather than blanking it out, and logs a warning to `data/refresh_log.json`. The OFAC+BIS fetch is a single HTTP call feeding 10 sub-lists — if it fails, all 10 fall back independently; if it succeeds but one sub-list looks suspiciously small (a sign the government's CSV schema changed), that one sub-list alone falls back while the other nine still update.

## One-time setup (about 10 minutes)

1. **Create a new, separate GitHub repo** (don't reuse the 4-source tool's repo).
2. **Push these files**, preserving the folder structure:
   ```
   bom_screener.html
   classify.py
   refresh_lists.py
   data/company_list.json         (already populated — today's data, works immediately)
   data/dow_1260h_state.json
   data/refresh_log.json
   .github/workflows/refresh.yml
   ```
3. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from a branch" → branch `main`, folder `/ (root)` → Save.
4. **Enable Actions** (usually on by default): Settings → Actions → General → allow workflows to run.
5. **Workflow permissions**: Settings → Actions → General → Workflow permissions → "Read and write permissions" (needed so it can commit the refreshed data back to the repo).
6. Done. Share the Pages URL:
   ```
   https://<your-org-or-username>.github.io/<repo-name>/bom_screener.html
   ```

⚠️ **Common setup mistake** (seen while deploying the 4-source tool): if you upload files via GitHub's web "Upload files" button rather than `git push`, folder structure sometimes gets flattened — `data/company_list.json` and `.github/workflows/refresh.yml` end up sitting loose in the repo root instead of in their subfolders. If the tool shows "Data failed to load" after deploying, check the repo's file tree first — `git push` or GitHub Desktop avoid this problem entirely.

## Important: must be served over http(s)

Same as the 4-source tool: this fetches `./data/company_list.json` at page load, which needs `http://`/`https://` (GitHub Pages, an intranet server, etc.) — **opening the HTML file directly from disk will not work**, browsers block that kind of local file-to-file fetch.

## Scope notes (things this tool still can't do)

- **FCC categorical bans** and **EU sectoral/trade restrictions** that apply by category/country-of-origin rather than by named entity — a name-matching tool structurally can't screen for "made in X" or "all state-owned enterprises above threshold Y" rules.
- **NDAA Sec. 5949** (FY2023 semiconductor procurement ban by country of origin) — same limitation.
- **DoW 1260H is not itself a blocking sanction** — it triggers separate DoW procurement restrictions (phased through 2027) and other downstream effects (AI-use ban, BIOSECURE-related restrictions); check the Reviewer Notes on each matched row for what the designation actually means before escalating.
- The **OFAC Palestinian Legislative Council List** commonly has 0 active entity-type rows most of the time (it's a predominantly individual-persons list, and this tool only screens organizations) — seeing 0 there is expected, not a bug.

## Files in this project

| File | Purpose |
|---|---|
| `bom_screener.html` | The screening tool. Fetches data at runtime — no data embedded in the file. |
| `refresh_lists.py` | The unattended refresh job covering all 7 categories / 14 sub-lists. Can also be run manually: `pip install beautifulsoup4 && python refresh_lists.py` |
| `classify.py` | Heuristic distinguishing companies from individuals for CSL rows where the source data doesn't tag "type" |
| `data/company_list.json` | The current data the tool loads. Rewritten by every refresh run. |
| `data/dow_1260h_state.json` | Persisted baseline + delta-tracking state for the DoW 1260H list specifically. |
| `data/refresh_log.json` | Machine-readable report of the last run: what worked, what fell back, and why. |
| `.github/workflows/refresh.yml` | The GitHub Actions schedule + commit-back logic. |
