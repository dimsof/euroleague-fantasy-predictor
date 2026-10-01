#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════
 fetch_injuries.py  —  Εξαγωγή injury report από basketnews.com
════════════════════════════════════════════════════════════════════
Παράγει: data/injuries.csv

Στήλες: player_id, player_name, minutes_factor

Minutes factors:
  Ready        → 1.0
  Expected     → 0.9
  Game-time    → 0.7
  Uncertain    → 0.5
  Questionable → 0.3
  Doubtful     → 0.25
  Out          → 0.0

Matching: ψάχνει κάθε injured player στο players_stats.xlsx
με βάση Surname (case-insensitive). Αν δεν βρει match,
εκτυπώνει warning.
"""

import os
import re
import sys
import pandas as pd

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Χρειάζεται: pip install requests beautifulsoup4")
    sys.exit(1)

# data/ βρίσκεται στο app/data/ → δύο επίπεδα πάνω από ratings/
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

URL = "https://basketnews.com/news-212393-euroleague-injury-report-updated.html"

# Status → minutes_factor
STATUS_FACTORS = {
    "ready":        1.0,
    "expected":     0.9,
    "game-time":    0.7,
    "uncertain":    0.5,
    "questionable": 0.3,
    "doubtful":     0.25,
    "out":          0.0,
}


def fetch_injury_page():
    """Κατεβάζει τη σελίδα του injury report.
    Αν αποτύχει (403/Cloudflare), ψάχνει τοπικό data/injuries.html."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        resp = requests.get(URL, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  ⚠ Fetch απέτυχε ({e})")
        # Fallback: τοπικό HTML αρχείο
        # Πρώτα ψάξε injuries.html, μετά οποιοδήποτε .html στο data/
        local_html = os.path.join(DATA_DIR, "injuries.html")
        if os.path.exists(local_html):
            print(f"  → Χρησιμοποιώ τοπικό: {local_html}")
            with open(local_html, "r", encoding="utf-8") as f:
                return f.read()
        # Ψάξε οποιοδήποτε .html (πχ αν ο browser το αποθήκευσε με άλλο όνομα)
        import glob
        html_files = glob.glob(os.path.join(DATA_DIR, "*.html"))
        if html_files:
            chosen = html_files[0]
            print(f"  → Βρήκα τοπικό HTML: {os.path.basename(chosen)}")
            with open(chosen, "r", encoding="utf-8") as f:
                return f.read()
        print(f"  ❌ Άνοιξε στον browser: {URL}")
        print(f"     Ctrl+S → Αποθήκευσε στο: {DATA_DIR}")
        sys.exit(1)


def parse_injuries(html):
    """
    Ψάχνει στη σελίδα τους παίκτες και το status τους.
    Επιστρέφει list of dicts: [{name, status, status_raw}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")

    injuries = []

    # Η σελίδα έχει labels με class που περιέχει το status
    # Ψάχνουμε για player names κοντά σε status badges
    # Προσπαθούμε πολλαπλά patterns

    # Pattern 1: Ψάξε για known status keywords στο text
    text = soup.get_text()

    # Pattern 2: Ψάξε divs/spans με status classes
    for status_key in STATUS_FACTORS:
        # Ψάξε elements που περιέχουν το status keyword
        elements = soup.find_all(
            string=re.compile(rf"\b{re.escape(status_key)}\b", re.IGNORECASE)
        )
        for el in elements:
            # Βρες τον parent container
            parent = el.parent
            if parent:
                # Ψάξε κοντινά elements για player name
                container = parent.parent
                if container:
                    # Πάρε όλο το text του container
                    full_text = container.get_text(separator=" ").strip()
                    injuries.append({
                        "container_text": full_text,
                        "status_raw": status_key,
                    })

    return injuries


def parse_injuries_simple(html):
    """
    Απλούστερο parsing: ψάχνει στο HTML για player entries.
    Κάθε παίκτης έχει όνομα + status badge.
    """
    soup = BeautifulSoup(html, "html.parser")
    injuries = []

    # Ψάξε για spans/divs με status text
    status_pattern = re.compile(
        r"\b(Ready|Expected|Questionable|Game-time|Doubtful|Out|Uncertain)\b",
        re.IGNORECASE
    )

    # Βρες όλα τα elements που περιέχουν status
    all_elements = soup.find_all(string=status_pattern)

    for el in all_elements:
        status_match = status_pattern.search(el)
        if not status_match:
            continue
        status = status_match.group(1).lower()

        # Ανέβα στο DOM για να βρεις player name
        parent = el
        for _ in range(10):
            parent = parent.parent
            if parent is None:
                break
            text = parent.get_text(separator="|").strip()
            # Ψάξε για pattern: Name | Status | details
            parts = [p.strip() for p in text.split("|") if p.strip()]
            if len(parts) >= 2:
                # Πρώτο μη-status part πιθανόν name
                for part in parts:
                    if not status_pattern.search(part) and len(part) > 3 and len(part) < 40:
                        injuries.append({
                            "name": part,
                            "status": status,
                        })
                        break
                break

    # Deduplicate
    seen = set()
    unique = []
    for inj in injuries:
        key = (inj["name"].lower(), inj["status"])
        if key not in seen:
            seen.add(key)
            unique.append(inj)

    return unique


def match_player_id(name, players_df):
    """
    Αντιστοιχίζει ένα όνομα injury report → Player_ID στο players_stats.
    Ψάχνει με Surname match (case-insensitive).
    Αν βρει πολλαπλά matches, δοκιμάζει και Name.
    Επιστρέφει (Player_ID, matched_name) ή (None, None).
    """
    # Split injury name σε parts
    parts = name.strip().split()
    if not parts:
        return None, None

    surname = parts[-1].upper()

    # Ψάξε στο Surname
    matches = players_df[players_df["Surname"].str.upper().str.strip() == surname]

    if len(matches) == 0:
        # Δοκίμασε με first name
        if len(parts) >= 2:
            first = parts[0].upper()
            matches = players_df[players_df["Name"].str.upper().str.strip() == first]

    if len(matches) == 1:
        r = matches.iloc[0]
        return r["Player_ID"], f"{r['Name']} {r['Surname']}"

    if len(matches) > 1 and len(parts) >= 2:
        # Φίλτρο και με first name
        first = parts[0].upper()
        refined = matches[matches["Name"].str.upper().str.strip() == first]
        if len(refined) == 1:
            r = refined.iloc[0]
            return r["Player_ID"], f"{r['Name']} {r['Surname']}"

    if len(matches) > 0:
        r = matches.iloc[0]
        return r["Player_ID"], f"{r['Name']} {r['Surname']}"

    return None, None


def fetch_injuries():
    """Κύρια συνάρτηση."""

    # ── 1. Φόρτωση players_stats ──
    ps_path = os.path.join(DATA_DIR, "players_stats.xlsx")
    ps = pd.read_excel(ps_path)
    ps = ps[ps["Name"].notna() & ps["Surname"].notna()].copy()
    ps = ps[ps["Position"].isin(["Guard", "Forward", "Center"])].copy()
    ps = ps[pd.to_numeric(ps["ID"], errors="coerce").notna()].copy()
    ps["Player_ID"] = "P" + ps["ID"].astype(int).astype(str).str.zfill(6)

    print(f"→ {len(ps)} παίκτες στο roster")

    # ── 2. Fetch page ──
    print(f"→ Fetching: {URL}")
    html = fetch_injury_page()
    print(f"→ Page size: {len(html)} bytes")

    # ── 3. Parse injuries ──
    injuries = parse_injuries_simple(html)
    print(f"→ Parsed: {len(injuries)} injury entries")

    if not injuries:
        print("⚠ Δεν βρέθηκαν injuries! Δοκιμάζω fallback parsing...")
        # Fallback: hardcoded parse αν το site αλλάξει structure
        print("⚠ Αν το site άλλαξε δομή, χρειάζεται update στο parser.")
        return

    # ── 4. Match με roster ──
    results = []
    unmatched = []

    for inj in injuries:
        pid, matched_name = match_player_id(inj["name"], ps)
        factor = STATUS_FACTORS.get(inj["status"], 0.5)

        if pid:
            results.append({
                "player_id": pid,
                "player_name": matched_name,
                "minutes_factor": factor,
            })
        else:
            unmatched.append(f"  ⚠ {inj['name']} ({inj['status']})")

    # ── 5. Output ──
    df = pd.DataFrame(results)

    if len(df):
        # Deduplicate (κράτα το χειρότερο factor αν εμφανίζεται 2 φορές)
        df = df.sort_values("minutes_factor").drop_duplicates("player_id", keep="first")
        df = df.sort_values("player_name")

    out_path = os.path.join(DATA_DIR, "injuries.csv")
    df.to_csv(out_path, index=False)

    print(f"\n✅ {out_path}: {len(df)} παίκτες")

    # Stats
    status_counts = {}
    for _, r in df.iterrows():
        f = r["minutes_factor"]
        for s, sv in STATUS_FACTORS.items():
            if sv == f:
                status_counts[s] = status_counts.get(s, 0) + 1
                break
    print(f"→ Status breakdown: {status_counts}")

    if unmatched:
        print(f"\n⚠ Unmatched ({len(unmatched)}):")
        for u in unmatched:
            print(u)

    return df


if __name__ == "__main__":
    fetch_injuries()
