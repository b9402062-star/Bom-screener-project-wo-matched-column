#!/usr/bin/env python3
"""
refresh_lists.py (FULL-COVERAGE EDITION)

Unattended refresh job for the BOM Restricted Party Screener (Full) Company
Review List. Designed to be run on a schedule (see .github/workflows/refresh.yml)
with no human involvement in the common case.

Covers SEVEN source categories:
  1. UFLPA Entity List              (DHS, scraped live)
  2. OFAC sanctions lists/programs  (Treasury, via trade.gov CSL)
  3. BIS restricted-party lists     (Commerce, via trade.gov CSL)
  4. NDAA Sec. 889 / FAR-DFARS      (static, 5 named entities)
  5. DoW Section 1260H List         (stateful delta-tracking via Federal Register API)
  6. FCC Covered List               (static - fcc.gov blocks automated fetches)
  7. EU Sanctioned Entities         (EU Financial Sanctions Files XML)

Writes:
  data/company_list.json      - the full, flat list of restricted-party rows
                                 the tool loads at runtime
  data/dow_1260h_state.json   - persisted state for the DoW Section 1260H
                                 delta-tracking (see fetch_dow_1260h below)
  data/refresh_log.json       - a short machine-readable run report: what
                                 succeeded, what fell back to prior data, and
                                 why - so a human CAN check in occasionally
                                 without having to.

Design principle: never let one failing source corrupt or blank out data for
the others. Each fetcher is wrapped in try/except; on failure we keep
whatever was already in the previous company_list.json for that source and
log a warning. The script's exit code is always 0 unless something outside
this contract goes wrong, so the GitHub Actions workflow never "fails
loudly" over a single flaky upstream site - it just commits whatever
improved and moves on.
"""
import csv
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from classify import looks_like_entity

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)
COMPANY_LIST_PATH = DATA_DIR / "company_list.json"
DOW_STATE_PATH = DATA_DIR / "dow_1260h_state.json"
LOG_PATH = DATA_DIR / "refresh_log.json"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
TODAY_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

UA = "Mozilla/5.0 (compatible; BOM-Restricted-Party-Screener-Refresh/1.0; +https://example.org/bot)"


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clean(s):
    return (s or "").strip()


def clean_ws(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def rec(source_list, entity, alias, etype, country, address, parent_notes,
        scope, date_added, url, rec_id, notes):
    return {
        "Source List": source_list,
        "Entity Name": entity,
        "Normalized Entity Name": "",
        "Alias / Known Name": alias,
        "Normalized Alias": "",
        "Entity Type": etype,
        "Country / Region": country,
        "Address": address,
        "Parent / Affiliate / Subsidiary Notes": parent_notes,
        "Product / Service Scope": scope,
        "Date Added": date_added,
        "Source URL": url,
        "Source Record ID": rec_id,
        "Source Last Checked": TODAY,
        "Active?": "Yes",
        "Reviewer Status": "Pending Review",
        "Reviewer": "",
        "Reviewer Date": "",
        "Reviewer Notes": notes,
    }


# ══════════════════════════════════════════════════════════════════
# 1. UFLPA Entity List - scraped from the official DHS page.
# ══════════════════════════════════════════════════════════════════
UFLPA_URL = "https://www.dhs.gov/uflpa-entity-list"

ALIAS_TRIGGER_RE = re.compile(
    r'\((?:and\s+\w+\s+alias(?:es)?|including\s+\w+\s+alias(?:es)?|also\s+known\s+as|formerly\s+known\s+as)'
    r'\s*:?\s*(.*)\)$',
    re.IGNORECASE,
)
SUFFIX_NOTE_RE = re.compile(
    r'\s+and\s+(its\s+)?(subordinate and affiliated entities|subsidiaries)\.?$', re.IGNORECASE
)

SECTION_SCOPE = {
    "2(d)(2)(B)(i)": "Forced labor / UFLPA - Section 2(d)(2)(B)(i) - mine/produce/manufacture with forced labor",
    "2(d)(2)(B)(ii)": "Forced labor / UFLPA - Section 2(d)(2)(B)(ii) - labor-transfer/recruitment out of Xinjiang",
    "2(d)(2)(B)(iv)": "Forced labor / UFLPA - Section 2(d)(2)(B)(iv) - exported products from PRC",
    "2(d)(2)(B)(v)": "Forced labor / UFLPA - Section 2(d)(2)(B)(v) - sourcing from XUAR / labor-transfer schemes",
}

UFLPA_NOTE = ("Scraped from the live DHS UFLPA Entity List page. Confirm against the official "
              "DHS page/Federal Register before compliance use.")


def parse_uflpa_cell(raw):
    raw = clean_ws(raw)
    notes = ""
    m_suffix = SUFFIX_NOTE_RE.search(raw)
    if m_suffix:
        notes = clean_ws(m_suffix.group(0))
        raw = raw[: m_suffix.start()].strip()

    aliases = []
    m = ALIAS_TRIGGER_RE.search(raw)
    if m:
        name = raw[: m.start()].strip()
        parts = [clean_ws(re.sub(r'^\s*and\s+', '', p, flags=re.IGNORECASE)) for p in m.group(1).split(';')]
        aliases = [p for p in parts if p]
    else:
        name = raw.strip()
    return name, aliases, notes


def find_section_code(table):
    node = table.find_previous("p")
    hops = 0
    while node is not None and hops < 6:
        a = node.find("a") if hasattr(node, "find") else None
        if a and "Section" in a.get_text():
            m = re.search(r"Section\s*([\d()a-zA-Z]+)", a.get_text())
            if m:
                return m.group(1)
        node = node.find_previous("p")
        hops += 1
    return None


def fetch_uflpa():
    from bs4 import BeautifulSoup

    html = http_get(UFLPA_URL, timeout=60).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for table in soup.find_all("table"):
        section = find_section_code(table)
        scope = SECTION_SCOPE.get(section, f"Forced labor / UFLPA - Section {section}" if section else "Forced labor / UFLPA")
        trs = table.find_all("tr")
        for tr in trs[1:]:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name_cell = tds[0].get_text(" ", strip=True)
            date_cell = tds[1].get_text(" ", strip=True)
            name, aliases, notes = parse_uflpa_cell(name_cell)
            if not name:
                continue
            records.append(rec(
                "UFLPA Entity List", name, "; ".join(aliases), "Entity", "China", "",
                notes, scope, date_cell, UFLPA_URL, "", UFLPA_NOTE,
            ))
    return records


# ══════════════════════════════════════════════════════════════════
# 2 & 3. OFAC + BIS - trade.gov Consolidated Screening List (CSL) bulk CSV
#    Official US government source, stable URL, updated hourly upstream.
# ══════════════════════════════════════════════════════════════════
CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"

SOURCE_MAP = {
    "Specially Designated Nationals (SDN) - Treasury Department": "OFAC SDN List",
    "Sectoral Sanctions Identifications List (SSI) - Treasury Department": "OFAC Sectoral Sanctions (SSI) List",
    "Non-SDN Menu-Based Sanctions List (NS-MBS List) - Treasury Department": "OFAC Non-SDN Menu-Based Sanctions List",
    "Non-SDN Chinese Military-Industrial Complex Companies List (CMIC) - Treasury Department": "OFAC Non-SDN Chinese Military-Industrial Complex (CMIC) List",
    "Palestinian Legislative Council List (PLC) - Treasury Department": "OFAC Palestinian Legislative Council List",
    "Capta List (CAP) - Treasury Department": "OFAC Capta List",
    "Entity List (EL) - Bureau of Industry and Security": "BIS Entity List",
    "Denied Persons List (DPL) - Bureau of Industry and Security": "BIS Denied Persons List",
    "Unverified List (UVL) - Bureau of Industry and Security": "BIS Unverified List",
    "Military End User (MEU) List - Bureau of Industry and Security": "BIS Military End User (MEU) List",
}

CSL_NOTES = {
    "OFAC SDN List": "Specially Designated Nationals and Blocked Persons List, via trade.gov's Consolidated Screening List. Organization/entity records only. Confirm current status against OFAC before compliance use.",
    "OFAC Sectoral Sanctions (SSI) List": "Activity-specific restrictions, not a full blocking sanction - confirm which directive applies before compliance use.",
    "OFAC Non-SDN Menu-Based Sanctions List": "A selected 'menu' of measures applies per entity, not a full blocking designation.",
    "OFAC Non-SDN Chinese Military-Industrial Complex (CMIC) List": "Investment-ban list under EO 13959/14032 restricting U.S. investment in listed securities/derivatives. Overlaps with, but is legally distinct from, the DoW Section 1260H list.",
    "OFAC Palestinian Legislative Council List": "Confirm current status against OFAC before compliance use.",
    "OFAC Capta List": "Correspondent/payable-through account restrictions only, not a full blocking sanction.",
    "BIS Entity List": "Presence triggers an EAR license requirement, often with a policy of denial. Confirm current terms against bis.gov before compliance use.",
    "BIS Denied Persons List": "Denial orders generally deny all export privileges. Orders with an end_date already in the past are excluded from this list (the CSL bulk file retains expired orders for archival search, but they are no longer in force) - only currently-active orders are included. Confirm current order terms/expiration against bis.gov.",
    "BIS Unverified List": "A 'Red Flag' BIS could not resolve in a prior transaction - not a per-se export prohibition.",
    "BIS Military End User (MEU) List": "Triggers an EAR license requirement for items subject to the EAR destined to that end user.",
}


def fetch_csl():
    """Returns a dict: {source_list_label: [records]} for all 10 OFAC+BIS sub-lists."""
    raw = http_get(CSL_URL, timeout=180).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    rows = [r for r in reader if r.get("source") in SOURCE_MAP]

    def keep(r):
        t = r.get("type", "")
        if t == "Entity":
            pass
        elif t in ("Individual", "Vessel", "Aircraft"):
            return False
        elif t == "":
            if not looks_like_entity(r.get("name", "")):
                return False
        else:
            return False

        # BIS Denied Persons List denial orders carry a fixed start_date/end_date
        # (unlike SDN/Entity List designations, which are open-ended until formally
        # delisted). The CSL bulk file keeps expired orders for historical/archival
        # search - trade.gov's own DPL page filters these out, and so must we, or
        # we'd flag companies whose denial order lapsed years ago as currently
        # restricted. Confirmed via a live discrepancy: "Realtek Semi-Conductor Co.
        # Ltd" appeared in our BIS DPL data with end_date 2000-08-03 (expired 26
        # years ago) despite not appearing on bis.gov's current DPL page.
        end_date = clean(r.get("end_date"))
        if end_date:
            try:
                if datetime.strptime(end_date, "%Y-%m-%d").date() < datetime.now(timezone.utc).date():
                    return False
            except ValueError:
                pass  # unparseable date - don't let a formatting quirk silently drop a real record

        return True

    by_source = {v: [] for v in SOURCE_MAP.values()}
    seen = set()
    for r in rows:
        if not keep(r):
            continue
        name = clean(r.get("name"))
        if not name:
            continue
        slist = SOURCE_MAP[r["source"]]
        key = (slist, name.upper())
        if key in seen:
            continue
        seen.add(key)

        scope_parts = [clean(r.get(f)) for f in ("programs", "license_requirement", "license_policy")]
        scope_parts = [p for p in scope_parts if p]
        scope = " | ".join(scope_parts) if scope_parts else slist
        end_date = clean(r.get("end_date"))
        if end_date:
            scope = f"{scope} (denial order in effect through {end_date})" if scope_parts else f"Denial order in effect through {end_date}"

        by_source[slist].append(rec(
            slist, name, clean(r.get("alt_names")), "Entity", "", clean(r.get("addresses")),
            clean(r.get("remarks")), scope, clean(r.get("start_date")),
            clean(r.get("source_list_url")) or clean(r.get("source_information_url")),
            clean(r.get("entity_number")) or clean(r.get("federal_register_notice")),
            CSL_NOTES[slist],
        ))
    return by_source


# ══════════════════════════════════════════════════════════════════
# 4. NDAA Sec. 889 / FAR-DFARS Covered Telecom - static, 5 named entities,
#    unchanged since 2019. Re-verify occasionally against FAR 52.204-25.
# ══════════════════════════════════════════════════════════════════
SEC889_URL = "https://www.acquisition.gov/far/52.204-25"
SEC889_NOTE = ("Named entities whose covered telecom/surveillance equipment is banned "
               "government-wide under FAR 52.204-25 / DFARS 252.204-7018/7020. Same companies "
               "as the FCC Covered List but tracked separately for FAR/DFARS-clause citation. "
               "Does NOT cover NDAA Sec. 5949 (FY2023), a country-of-origin (not named-entity) "
               "semiconductor procurement ban this tool cannot screen for.")
SEC889_STATIC = [
    ("Huawei Technologies Co., Ltd.", "Huawei", "Telecommunications and networking equipment"),
    ("ZTE Corporation", "ZTE", "Telecommunications equipment"),
    ("Hytera Communications Corporation", "Hytera", "Radio/telecommunications equipment for public safety"),
    ("Hangzhou Hikvision Digital Technology Co., Ltd.", "Hikvision", "Video surveillance equipment"),
    ("Dahua Technology Co., Ltd.", "Dahua", "Video surveillance equipment"),
]


def static_sec889():
    return [
        rec("NDAA Sec. 889 / FAR-DFARS Covered Telecom", name, alias, "Entity", "China", "",
            "Includes subsidiaries and affiliates per Section 889(f)(3) definition",
            f"Covered telecommunications equipment/services (Section 889 FY2019 NDAA) - {scope}",
            "2019-08-13", SEC889_URL, "", SEC889_NOTE)
        for name, alias, scope in SEC889_STATIC
    ]


# ══════════════════════════════════════════════════════════════════
# 5. DoW Section 1260H List - stateful delta tracking via the Federal
#    Register API (official, free, no CORS/licensing concerns).
# ══════════════════════════════════════════════════════════════════
FR_SEARCH_URL = ("https://www.federalregister.gov/api/v1/documents.json"
                  "?conditions%5Bterm%5D=Designation+of+Chinese+Military+Companies"
                  "&conditions%5Bagencies%5D%5B%5D=defense-department"
                  "&conditions%5Btype%5D%5B%5D=NOTICE&order=newest&per_page=5")

DOW_NOTE = ("Section 1260H List of Chinese Military Companies (NDAA FY2021 Sec. 1260H), tracked "
            "via the Federal Register API with delta updates applied automatically when a new "
            "DoW notice is detected. NOT a blocking sanction - triggers DoW procurement "
            "restrictions, a DoW-contract AI-use ban, and related BIOSECURE/hardware-procurement "
            "restrictions, and is a red flag for EAR military end-user status.")


def clean_fr_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\[\[Page \d+\]\]', '', text)
    return text


def parse_1260h_notice(text):
    """Best-effort parse of a 'Notice of Availability of Designation of Chinese
    Military Companies' Federal Register notice. Returns (added, removed) where
    added is a list of {"name": ..., "short": ...} dicts and removed is a list
    of plain name strings. Returns (None, None) if the expected structure isn't
    found (caller should treat this as a failed parse and not apply anything)."""
    text = clean_fr_text(text)

    added_marker = re.search(r'The Deputy Secretary of Defense has determined that the following\s*\n\s*entities qualify', text)
    removed_marker = re.search(r'The Deputy Secretary of Defense has determined that the following\s*\n\s*previously listed entities should be removed', text)
    if not added_marker:
        return None, None

    added_colon = text.index(':', added_marker.start())
    added_end = removed_marker.start() if removed_marker else len(text)
    added_text = text[added_colon + 1: added_end]

    removed_names = []
    if removed_marker:
        removed_colon = text.index(':', removed_marker.start())
        tail = text[removed_colon + 1: removed_colon + 3000]
        end_m = re.search(r'\n\s{2,}[A-Z][a-z]', tail)
        removed_block = tail[: end_m.start()] if end_m else tail
        removed_names = [clean_ws(ln) for ln in removed_block.split('\n') if clean_ws(ln)]

    paras = [p.strip() for p in re.split(r'\n\s*\n', added_text) if p.strip()]
    raw_entities = []
    for p in paras:
        cp = clean_ws(p)
        if '1260H(g)' in cp:
            continue
        if re.match(r'^(is|are|were|has|have)\b', cp):
            continue
        raw_entities.append(cp)

    merged = []
    for e in raw_entities:
        if e.startswith('(') and merged:
            merged[-1] = merged[-1] + ' ' + e
        else:
            merged.append(e)

    added = []
    for block in merged:
        m = re.match(r'^(.*?)\(([^()]+)\)', block)
        if m:
            added.append({"name": m.group(1).strip().rstrip(','), "short": m.group(2).strip()})
        else:
            added.append({"name": block, "short": ""})

    return added, removed_names


def fetch_dow_1260h(log):
    state = {"last_processed_document_number": None, "entities": []}
    if DOW_STATE_PATH.exists():
        try:
            state = json.loads(DOW_STATE_PATH.read_text())
        except Exception as e:
            log["warnings"].append(f"DoW 1260H: could not read prior state file ({e}); starting fresh.")

    try:
        search_raw = http_get(FR_SEARCH_URL, timeout=30)
        search = json.loads(search_raw)
        latest = search["results"][0]
        doc_number = latest["document_number"]

        if doc_number != state.get("last_processed_document_number"):
            detail_raw = http_get(
                f"https://www.federalregister.gov/api/v1/documents/{doc_number}.json"
                f"?fields[]=raw_text_url&fields[]=publication_date", timeout=30
            )
            detail = json.loads(detail_raw)
            notice_text = http_get(detail["raw_text_url"], timeout=30).decode("utf-8", errors="replace")
            added, removed = parse_1260h_notice(notice_text)

            if added is None or len(added) == 0:
                log["warnings"].append(
                    f"DoW 1260H: found newer notice {doc_number} but could not confidently parse "
                    f"its entity list - leaving prior baseline of {len(state['entities'])} entities "
                    f"unchanged. Please check {latest['html_url']} manually."
                )
            else:
                existing_names = {e["name"].upper() for e in state["entities"]}
                removed_upper = {r.upper() for r in (removed or [])}
                state["entities"] = [e for e in state["entities"] if e["name"].upper() not in removed_upper]
                added_count = 0
                for a in added:
                    if a["name"].upper() not in existing_names:
                        state["entities"].append({
                            "name": a["name"], "short": a.get("short", ""),
                            "date_added": detail.get("publication_date", TODAY_DATE),
                            "source_notice": doc_number,
                        })
                        added_count += 1
                state["last_processed_document_number"] = doc_number
                log["info"].append(
                    f"DoW 1260H: applied notice {doc_number} - added {added_count}, "
                    f"removed {len(removed or [])}. New total: {len(state['entities'])}."
                )
        else:
            log["info"].append(f"DoW 1260H: no newer notice than {doc_number}; baseline unchanged.")

    except Exception as e:
        log["warnings"].append(f"DoW 1260H: refresh check failed ({e}); keeping prior baseline of "
                                f"{len(state['entities'])} entities.")

    DOW_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    fr_notice_url = ("https://www.federalregister.gov/documents/search?conditions%5Bterm%5D="
                      "Designation+of+Chinese+Military+Companies")
    records = [
        rec("DoW Section 1260H List (Chinese Military Companies)", e["name"], e.get("short", ""),
            "Entity", "China", "", "Designated 'Chinese military company' under Sec. 1260H NDAA FY2021.",
            "Designated 'Chinese military company' under Sec. 1260H NDAA FY2021 - not a blocking "
            "sanction; triggers DoW procurement/AI-use restrictions and EAR military end-user "
            "due-diligence flag.",
            e.get("date_added", ""), fr_notice_url, e.get("source_notice", ""), DOW_NOTE)
        for e in state["entities"]
    ]
    return records


# ══════════════════════════════════════════════════════════════════
# 6. FCC Covered List - fcc.gov actively blocks automated fetches (WAF),
#    and this list is small and rarely changes. Kept as a maintained
#    static list rather than scraped. Review periodically at the URL below.
# ══════════════════════════════════════════════════════════════════
FCC_URL = "https://www.fcc.gov/supplychain/coveredlist"
FCC_NOTE = ("fcc.gov blocks automated/bot fetches (WAF), so this source is maintained as a "
            "static list rather than scraped live - review " + FCC_URL + " periodically for "
            "changes. Since 2025 the FCC has also added several CATEGORICAL bans not tied "
            "to a named entity (routers - Mar 2026; UAS/drone components - Dec 2025; power "
            "inverters/robotics - Jul 2026) that this name-matching tool cannot represent.")

FCC_STATIC = [
    ("Huawei Technologies Company", "Huawei", "2021-03-12", "FCC-001", "Telecommunications equipment and video surveillance equipment (and subsidiaries/affiliates)"),
    ("ZTE Corporation", "ZTE", "2021-03-12", "FCC-002", "Telecommunications equipment and video surveillance equipment (and subsidiaries/affiliates)"),
    ("Hytera Communications Corporation", "Hytera", "2021-03-12", "FCC-003", "Telecommunications equipment and video surveillance equipment for public safety/national security (and subsidiaries/affiliates)"),
    ("Hangzhou Hikvision Digital Technology Company", "Hikvision", "2021-03-12", "FCC-004", "Video surveillance equipment for public safety/critical infrastructure (and subsidiaries/affiliates)"),
    ("Dahua Technology Company", "Dahua", "2021-03-12", "FCC-005", "Video surveillance equipment for public safety/critical infrastructure (and subsidiaries/affiliates)"),
    ("AO Kaspersky Lab", "Kaspersky Lab; Kaspersky", "2022", "FCC-006", "Information security and cybersecurity products and services"),
    ("China Mobile International USA Inc.", "China Mobile", "2022", "FCC-007", "Telecommunications services - barred from providing certain services in the US"),
    ("China Telecom (Americas) Corp.", "China Telecom Americas", "2022", "FCC-008", "Telecommunications services - barred from providing certain services in the US"),
    ("China Unicom (Americas) Operations Limited", "China Unicom Americas", "2022", "FCC-009", "Telecommunications services - barred from providing certain services in the US"),
    ("Pacific Networks Corp. & ComNet (USA) LLC", "Pacific Networks; ComNet", "2022", "FCC-010", "Telecommunications services - barred from providing certain services in the US"),
    ("Digitalsystem Technology Inc.", "Digitalsystem", "2026-07-07", "FCC-011", "International telecommunications services subject to Section 214 of the Communications Act of 1934"),
]


def static_fcc():
    return [
        rec("FCC Covered List", name, alias, "Entity", "China", "", "", scope, date_added,
            FCC_URL, rec_id, FCC_NOTE)
        for name, alias, date_added, rec_id, scope in FCC_STATIC
    ]


# ══════════════════════════════════════════════════════════════════
# 7. EU Sanctioned Entities - EU Financial Sanctions Files (FSF) XML
#    Official EU source, stable fixed-token URL, no CORS/licensing issues.
# ══════════════════════════════════════════════════════════════════
EU_FSF_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"

PROGRAMME_MAP = {
    'UKR': 'EU Russia/Ukraine territorial-integrity sanctions regime',
    'RUS': 'EU Russia sanctions regime',
    'RUSDA': 'EU Russia sanctions regime (destabilising activities)',
    'IRN': 'EU Iran sanctions regime (nuclear/human rights/drones)',
    'PRK': 'EU North Korea (DPRK) sanctions regime',
    'TAQA': 'EU Syria sanctions regime',
    'SYR': 'EU Syria sanctions regime',
    'BLR': 'EU Belarus sanctions regime',
    'HR': 'EU Global Human Rights sanctions regime',
    'TERR': 'EU terrorism sanctions regime (autonomous)',
    'MMR': 'EU Myanmar/Burma sanctions regime',
    'LBY': 'EU Libya sanctions regime',
    'COD': 'EU Democratic Republic of the Congo sanctions regime',
    'IRQ': 'EU Iraq sanctions regime',
    'SDNZ': 'EU Sudan sanctions regime',
    'CYB': 'EU Cyber-attacks sanctions regime',
    'EUAQ': 'EU ISIL (Da\u2019esh) and Al-Qaida sanctions regime',
    'CHEM': 'EU Chemical Weapons sanctions regime',
    'AFG': 'EU Afghanistan sanctions regime',
    'MDA': 'EU Moldova sanctions regime',
    'NIC': 'EU Nicaragua sanctions regime',
    'HAM': 'EU Hamas/Gaza-related sanctions regime',
    'UNLI': 'EU Lebanon-related (UN) sanctions regime',
    'SOM': 'EU Somalia sanctions regime',
    'CAF': 'EU Central African Republic sanctions regime',
    'YEM': 'EU Yemen sanctions regime',
    'GTM': 'EU Guatemala-related sanctions regime',
}

EU_NOTE = ("Extracted from the official EU Financial Sanctions Files (FSF) consolidated "
           "asset-freeze/travel-ban list. 'Enterprise' (organization) records only - "
           "individuals excluded since this tool screens BOM manufacturer/supplier names. "
           "Does NOT cover EU sectoral/trade restrictions that apply by category rather than "
           "by named entity. Confirm entity type and current legal effect against the EU "
           "Sanctions Map before compliance use.")


def fetch_eu_fsf():
    import xml.etree.ElementTree as ET

    raw = http_get(EU_FSF_URL, timeout=120)
    root = ET.fromstring(raw)
    ns = {"e": "http://eu.europa.ec/fpi/fsd/export"}
    entities = root.findall("e:sanctionEntity", ns)
    enterprises = [
        e for e in entities
        if e.find("e:subjectType", ns) is not None and e.find("e:subjectType", ns).get("code") == "enterprise"
    ]

    records = []
    for ent in enterprises:
        ref = ent.get("euReferenceNumber") or ""
        alias_info = []
        for na in ent.findall("e:nameAlias", ns):
            wn = clean(na.get("wholeName"))
            if not wn:
                continue
            lang = na.get("nameLanguage") or ""
            strong = na.get("strong") == "true"
            alias_info.append((wn, lang, strong))

        def pick(pred):
            for wn, lang, strong in alias_info:
                if pred(wn, lang, strong):
                    return wn
            return None

        primary = (pick(lambda wn, l, s: l == "EN" and s) or
                   pick(lambda wn, l, s: l == "EN") or
                   pick(lambda wn, l, s: l == "" and s) or
                   pick(lambda wn, l, s: l == "") or
                   (alias_info[0][0] if alias_info else None))
        if not primary:
            continue

        seen = {primary}
        alias_list = []
        for wn, lang, strong in alias_info:
            if lang in ("EN", "") and wn not in seen:
                seen.add(wn)
                alias_list.append(wn)
        alias_list = alias_list[:8]

        regs = ent.findall("e:regulation", ns)
        programmes, dates, urls = [], [], []
        for r in regs:
            p = r.get("programme")
            if p:
                programmes.append(p)
            d = r.get("entryIntoForceDate") or r.get("publicationDate")
            if d:
                dates.append(d)
            u = r.find("e:publicationUrl", ns)
            if u is not None and u.text:
                urls.append(u.text.strip())
        programmes = list(dict.fromkeys(programmes))
        scope = ("; ".join(PROGRAMME_MAP.get(p, f"EU sanctions regime ({p})") for p in programmes)
                 if programmes else "EU sanctions - asset freeze / travel ban (programme unspecified)")
        date_added = ent.get("designationDate") or (min(dates) if dates else "")
        source_url = urls[-1] if urls else "https://data.europa.eu/apps/eusanctionstracker/entities/"

        addr_el = ent.find("e:address", ns)
        country, address_str = "", ""
        if addr_el is not None:
            country = clean(addr_el.get("countryDescription")) or clean(addr_el.get("countryIso2Code"))
            parts = [clean(addr_el.get("street")), clean(addr_el.get("city")), clean(addr_el.get("zipCode"))]
            address_str = ", ".join(p for p in parts if p)
        if not country:
            cit = ent.find("e:citizenship", ns)
            if cit is not None:
                country = clean(cit.get("countryDescription")) or clean(cit.get("countryIso2Code"))

        remark_el = ent.find("e:remark", ns)
        remark = clean(remark_el.text) if remark_el is not None and remark_el.text else ""

        records.append(rec(
            "EU Sanctioned Entities", primary, "; ".join(alias_list), "Entity",
            country, address_str, remark, scope, date_added, source_url, ref, EU_NOTE
        ))
    return records


# ══════════════════════════════════════════════════════════════════
# Orchestration
# ══════════════════════════════════════════════════════════════════
def load_prior_by_source():
    if not COMPANY_LIST_PATH.exists():
        return {}
    try:
        prior = json.loads(COMPANY_LIST_PATH.read_text())
    except Exception:
        return {}
    by_source = {}
    for row in prior:
        by_source.setdefault(row["Source List"], []).append(row)
    return by_source


def main():
    log = {"run_started": TODAY, "info": [], "warnings": [], "errors": []}
    prior_by_source = load_prior_by_source()
    all_records = []

    # --- Simple single-source fetchers ---
    simple_fetchers = [
        ("UFLPA Entity List", fetch_uflpa, 100),
        ("FCC Covered List", static_fcc, 5),
        ("NDAA Sec. 889 / FAR-DFARS Covered Telecom", static_sec889, 5),
        ("EU Sanctioned Entities", fetch_eu_fsf, 1000),
    ]
    for label, fetcher, min_expected in simple_fetchers:
        try:
            result = fetcher()
            if len(result) < min_expected:
                raise ValueError(f"only {len(result)} rows parsed, expected >= {min_expected} - "
                                  f"likely a broken scraper/parser, not a real data shrink")
            all_records.extend(result)
            log["info"].append(f"{label}: OK ({len(result)} rows)")
        except Exception as e:
            fallback = prior_by_source.get(label, [])
            all_records.extend(fallback)
            log["warnings"].append(
                f"{label}: fetch/parse FAILED ({e}); kept {len(fallback)} rows from previous run."
            )

    # --- CSL (OFAC + BIS): one HTTP fetch feeding 10 sub-lists; treat each
    #     sub-list's success/failure independently so one bad label doesn't
    #     blank out the other nine.
    csl_labels = list(SOURCE_MAP.values())
    csl_min_expected = {
        "OFAC SDN List": 5000, "BIS Entity List": 1000, "BIS Denied Persons List": 20,
        "OFAC Sectoral Sanctions (SSI) List": 100, "BIS Unverified List": 50,
        "BIS Military End User (MEU) List": 20, "OFAC Non-SDN Chinese Military-Industrial Complex (CMIC) List": 20,
        "OFAC Non-SDN Menu-Based Sanctions List": 1, "OFAC Palestinian Legislative Council List": 0,
        "OFAC Capta List": 0,
    }
    try:
        csl_by_source = fetch_csl()
        for label in csl_labels:
            result = csl_by_source.get(label, [])
            min_expected = csl_min_expected.get(label, 0)
            if len(result) < min_expected:
                fallback = prior_by_source.get(label, [])
                all_records.extend(fallback)
                log["warnings"].append(
                    f"{label}: only {len(result)} rows parsed (expected >= {min_expected}) - "
                    f"kept {len(fallback)} rows from previous run instead."
                )
            else:
                all_records.extend(result)
                log["info"].append(f"{label}: OK ({len(result)} rows)")
    except Exception as e:
        for label in csl_labels:
            fallback = prior_by_source.get(label, [])
            all_records.extend(fallback)
        log["warnings"].append(
            f"OFAC+BIS (Consolidated Screening List): fetch FAILED ({e}); kept prior data for all "
            f"{len(csl_labels)} affected sub-lists."
        )

    # --- DoW 1260H has its own stateful diff-tracking flow ---
    try:
        dow_records = fetch_dow_1260h(log)
        all_records.extend(dow_records)
    except Exception as e:
        fallback = prior_by_source.get("DoW Section 1260H List (Chinese Military Companies)", [])
        all_records.extend(fallback)
        log["errors"].append(f"DoW 1260H: unexpected failure ({e}); kept {len(fallback)} rows from previous run.")

    COMPANY_LIST_PATH.write_text(json.dumps(all_records, ensure_ascii=False))
    log["run_finished"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log["total_rows"] = len(all_records)
    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    print(json.dumps(log, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
