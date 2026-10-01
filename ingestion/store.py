# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════
 store.py — ΑΠΟΘΗΚΕΥΩ ΤΑ ΝΕΑ ΔΕΔΟΜΕΝΑ ΜΕ ΤΑ ΥΠΟΛΟΙΠΑ ΤΗΣ ΧΡΟΝΙΑΣ
════════════════════════════════════════════════════════════════════
ΤΙ ΚΑΝΕΙ:
  Παίρνει όλα τα per-match "..._players.csv" (που έφτιαξε το fetch.py)
  και τα ΕΝΩΝΕΙ σε ΕΝΑ ετήσιο αρχείο:  data/boxscores_{σεζόν}.csv
  — στην ίδια μορφή που διαβάζει το reading/data_loader.py, ώστε τα νέα
  δεδομένα να ρέουν κατευθείαν στο υπόλοιπο σύστημα.

ΑΣΦΑΛΕΙΑ (dedup):
  Αν ένα ματς κατέβηκε δύο φορές, ΔΕΝ διπλογράφεται. Το κλειδί
  μοναδικότητας είναι (Season, Gamecode, Player_ID) — κρατάμε την
  τελευταία (πιο πρόσφατη) εκδοχή.
"""

import glob
import os

import pandas as pd

import config

_KEY = ["Season", "Gamecode", "Player_ID"]


def store_season(season):
    """
    Ενώνει τα per-match players της σεζόν στο ετήσιο boxscores.
    Επιστρέφει: (πλήθος_γραμμών, πλήθος_αγώνων) στο τελικό αρχείο.
    """
    pattern = os.path.join(config.MATCHES_DIR, f"S{season}_*_players.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"Δεν βρήκα per-match αρχεία στο {config.MATCHES_DIR}\n"
            f"   (έτρεξες πρώτα το fetch;)")

    # 1) διάβασε & ένωσε όλα τα νέα ματς
    new_rows = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

    # 2) αν υπάρχει ήδη ετήσιο boxscores, πρόσθεσέ το από κάτω
    out_path = config.boxscores_file(season)
    if os.path.exists(out_path):
        existing = pd.read_csv(out_path)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows

    # 3) dedup: κράτα την τελευταία εκδοχή κάθε (Season, Gamecode, Player_ID)
    have_key = [k for k in _KEY if k in combined.columns]
    before = len(combined)
    if have_key:
        combined = combined.drop_duplicates(subset=have_key, keep="last")
    removed = before - len(combined)

    # 4) ταξινόμηση για ευανάγνωστο αρχείο
    sort_cols = [c for c in ["Round", "Gamecode", "Team"] if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols).reset_index(drop=True)

    combined.to_csv(out_path, index=False)

    games = combined["Gamecode"].nunique() if "Gamecode" in combined.columns else "?"
    print(f"✓ {out_path}")
    print(f"  {len(combined)} γραμμές | {games} αγώνες | "
          f"{len(files)} per-match αρχεία διαβάστηκαν"
          + (f" | {removed} διπλά αφαιρέθηκαν" if removed else ""))
    return len(combined), games
