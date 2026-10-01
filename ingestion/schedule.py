# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════
 schedule.py — ΠΡΟΓΡΑΜΜΑ ΣΕΖΟΝ + ΑΥΤΟΜΑΤΑ FIXTURES ΑΝΑ ΑΓΩΝΙΣΤΙΚΗ
════════════════════════════════════════════════════════════════════
ΤΙ ΚΑΝΕΙ:
  1) fetch_schedule()  → κατεβάζει ΟΛΑ τα ματς από το API
  2) fixtures_for_round() → παράγει ΑΥΤΟΜΑΤΑ το fixtures.csv
     για μια δεδομένη αγωνιστική, χωρίς χειροκίνητη εισαγωγή.

ΧΡΗΣΗ:
  Αφού έχεις schedule_2026.csv:
    python run_ratings.py --round 1
  και το σύστημα ξέρει ΠΟΥ ΚΑΙ ΠΟΙΟΣ παίζει.
"""

import os
import time

import pandas as pd

import config


# ── ΚΑΤΕΒΑΣΜΑ ΠΡΟΓΡΑΜΜΑΤΟΣ ──────────────────────────────────────────

def fetch_schedule(season=None):
    """
    Κατεβάζει το πρόγραμμα της σεζόν → data/schedule_{season}.csv

    Δοκιμάζει πρώτα το euroleague-api (αν είναι εγκατεστημένο),
    αλλιώς χρησιμοποιεί απευθείας το API v2.
    """
    if season is None:
        season = config.CURRENT_SEASON

    out_path = config.schedule_file(season)

    # ΜΕΘΟΔΟΣ 1: euroleague-api package
    try:
        from euroleague_api.schedule import Schedule
        print(f"→ Κατεβάζω το πρόγραμμα (euroleague-api) σεζόν {season} ...")
        sched = Schedule()
        for attempt in range(5):
            try:
                df = sched.get_schedule(season)
                break
            except Exception as ex:
                if ("429" in str(ex) or "Too Many" in str(ex)) and attempt < 4:
                    print(f"   429 rate-limit, περιμένω 8s ({attempt+1}/5)")
                    time.sleep(8)
                else:
                    raise
        else:
            raise RuntimeError("Δεν κατέβηκε το πρόγραμμα (rate-limit).")
        df.to_csv(out_path, index=False)
        print(f"✓ {out_path}  ({len(df)} ματς)")
        return out_path
    except ImportError:
        pass

    # ΜΕΘΟΔΟΣ 2: απευθείας API v2 (δεν χρειάζεται euroleague-api)
    try:
        import urllib.request
        import json

        season_code = f"E{season}"
        url = f"https://api-live.euroleague.net/v2/competitions/E/seasons/{season_code}/games"
        print(f"→ Κατεβάζω το πρόγραμμα (API v2) σεζόν {season} ...")

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        games = []
        for g in data["data"]:
            games.append({
                "Round": g["round"],
                "Date": g.get("utcDate", ""),
                "Home": g["local"]["club"].get("tvCode", g["local"]["club"]["code"]),
                "Away": g["road"]["club"].get("tvCode", g["road"]["club"]["code"]),
                "HomeName": g["local"]["club"].get("abbreviatedName", ""),
                "AwayName": g["road"]["club"].get("abbreviatedName", ""),
            })

        df = pd.DataFrame(games)
        df = df.sort_values(["Round", "Date"]).reset_index(drop=True)
        df.to_csv(out_path, index=False)
        print(f"✓ {out_path}  ({len(df)} ματς, {df['Round'].nunique()} γύροι)")
        return out_path
    except Exception as ex:
        print(f"⚠ API v2 απέτυχε: {ex}")
        raise RuntimeError(
            f"Δεν κατέβηκε το πρόγραμμα. Βάλε χειροκίνητα το schedule_{season}.csv "
            f"στο {config.DATA_DIR}."
        )


# ── ΑΥΤΟΜΑΤΑ FIXTURES ΑΠΟ SCHEDULE ─────────────────────────────────

def load_schedule(season=None):
    """Φορτώνει το schedule CSV. Αν δεν υπάρχει, πετάει FileNotFoundError."""
    if season is None:
        season = config.CURRENT_SEASON
    path = config.schedule_file(season)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Δεν βρήκα το πρόγραμμα: {path}\n"
            f"Τρέξε:  python run_update.py --schedule  ή βάλ' το χειροκίνητα."
        )
    return pd.read_csv(path)


def fixtures_for_round(round_num, season=None):
    """
    Παράγει ΑΥΤΟΜΑΤΑ το fixtures.csv για τη δοσμένη αγωνιστική.

    Επιστρέφει: path στο fixtures.csv
    """
    schedule = load_schedule(season)
    md = schedule[schedule["Round"] == round_num]

    if md.empty:
        raise ValueError(
            f"Δεν βρέθηκαν αγώνες για Round {round_num}.\n"
            f"Διαθέσιμα: {sorted(schedule['Round'].unique().tolist())}"
        )

    # Φτιάχνουμε δύο γραμμές ανά αγώνα: μία για κάθε ομάδα
    rows = []
    for _, game in md.iterrows():
        rows.append({"team": game["Home"], "opp": game["Away"], "home": 1})
        rows.append({"team": game["Away"], "opp": game["Home"], "home": 0})

    fixtures = pd.DataFrame(rows)
    fixtures.to_csv(config.FIXTURES_FILE, index=False)

    print(f"\n✅ Αγωνιστική {round_num}: {len(md)} αγώνες → {config.FIXTURES_FILE}")
    for _, game in md.iterrows():
        print(f"   {game['Home']:>3s} vs {game['Away']:<3s}  ({game.get('HomeName','')} - {game.get('AwayName','')})")

    return config.FIXTURES_FILE


def list_rounds(season=None):
    """Εμφανίζει συνοπτικά όλους τους γύρους και τις ημερομηνίες τους."""
    schedule = load_schedule(season)
    for rnd in sorted(schedule["Round"].unique()):
        md = schedule[schedule["Round"] == rnd]
        dates = pd.to_datetime(md["Date"]).dt.date.unique()
        date_str = " / ".join(str(d) for d in sorted(dates))
        print(f"  Round {rnd:>2d}:  {len(md)} ματς  ({date_str})")
