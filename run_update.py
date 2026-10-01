#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
▶ ΤΡΕΧΕΙΣ ΑΥΤΟ — ενημερώνει τα δεδομένα από το ίντερνετ.

Κάνει δύο δουλειές με τη σειρά:
  1. fetch  → κατεβάζει τα ματς → per-match αρχεία στο data/matches/
  2. store  → τα ενσωματώνει στο data/boxscores_{σεζόν}.csv

⚠ Χρειάζεται internet + το πακέτο euroleague-api (pip install euroleague-api).
   Τρέξε το στο PC σου.

ΧΡΗΣΗ:
    python run_update.py                    # τρέχουσα σεζόν (2026-27) — όλη ως τώρα
    python run_update.py --round 5          # μόνο ο γύρος 5
    python run_update.py --schedule         # + κατέβασε & το πρόγραμμα
    python run_update.py --store-only       # μόνο συγχώνευση (χωρίς net)
    python run_update.py --season 2024      # (προαιρετικά) άλλη σεζόν
"""

import argparse

import config
from ingestion import fetch, store, schedule


def main():
    p = argparse.ArgumentParser(description="Ενημέρωση δεδομένων EuroLeague.")
    p.add_argument("--season", type=int, default=config.CURRENT_SEASON,
                   help=f"έτος έναρξης· default {config.CURRENT_SEASON} (=2025-26)")
    p.add_argument("--round", type=int, default=None, help="μόνο συγκεκριμένος γύρος")
    p.add_argument("--schedule", action="store_true", help="κατέβασε & το πρόγραμμα")
    p.add_argument("--store-only", action="store_true",
                   help="μόνο συγχώνευση των ήδη κατεβασμένων (χωρίς internet)")
    p.add_argument("--pause", type=float, default=6.0,
                   help="δευτ. παύσης ΑΝΑ ΠΑΙΧΝΙΔΙ (μεγαλύτερο = λιγότερα 429· default 6)")
    p.add_argument("--force", action="store_true",
                   help="ξανακατέβασε ΚΑΙ τα ήδη κατεβασμένα παιχνίδια")
    args = p.parse_args()

    if args.schedule:
        schedule.fetch_schedule(args.season)
        print()

    if not args.store_only:
        fetch.fetch_season(args.season, only_round=args.round,
                           pause=args.pause, force=args.force)
        print()

    print("→ Συγχωνεύω στο ετήσιο boxscores...")
    store.store_season(args.season)
    print("\n✅ Έτοιμο. Τώρα μπορείς να τρέξεις:")
    print(f"   python run_ratings.py --box data/boxscores_{args.season}.csv")


if __name__ == "__main__":
    main()
