#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════════
 Script 1: build_anchor_backtest.py
══════════════════════════════════════════════════════════════════
Δέχεται αγωνιστική Ν και φτιάχνει cumulative anchor (rounds 1..N).

Για τον ΚΑΘΕ παίκτη, για ΚΑΘΕ PIR component ανά 40 min:
  - EWMA (span=5) → τρέχον επίπεδο
  - std (κανονική τυπική απόκλιση)
  - GP (πόσα ματς έπαιξε)

Output: test/anchors/anchor_round_{N}.csv

Χρήση:
  python build_anchor_backtest.py --round 5          # μόνο round 5
  python build_anchor_backtest.py --all               # rounds 5..max (για backtest)
  python build_anchor_backtest.py --from 5 --to 37    # rounds 5..37
"""

import os
import sys
import argparse
import pandas as pd

# ── Config ─────────────────────────────────────────────────────
EWMA_SPAN = 5

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ANCHORS_DIR = os.path.join(TEST_DIR, "anchors")
BOXSCORES_FILE = os.path.join(TEST_DIR, "boxscores_2025.csv")

RAW_TO_40 = {
    'FieldGoalsMade2':      'FG2M_40',
    'FieldGoalsAttempted2': 'FG2A_40',
    'FieldGoalsMade3':      'FG3M_40',
    'FieldGoalsAttempted3': 'FG3A_40',
    'FreeThrowsMade':       'FTM_40',
    'FreeThrowsAttempted':  'FTA_40',
    'TotalRebounds':        'REB_40',
    'Assistances':          'AST_40',
    'Steals':               'STL_40',
    'BlocksFavour':         'BLK_40',
    'Turnovers':            'TOV_40',
    'FoulsReceived':        'FD_40',
    'FoulsCommited':        'FC_40',
    'BlocksAgainst':        'BA_40',
}

COMPONENT_STATS = list(RAW_TO_40.values())
ALL_P40_COLS = COMPONENT_STATS + ['PIR_per_40']


def parse_minutes(val):
    if pd.isna(val) or str(val).strip().upper() == "DNP":
        return 0.0
    s = str(val).strip()
    if ":" in s:
        parts = s.split(":")
        return int(parts[0]) + int(parts[1]) / 60.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def build_anchor(df_all, up_to_round):
    """
    Φτιάχνει cumulative anchor από rounds 1..up_to_round.
    Για κάθε παίκτη, κάθε component per 40:
      - EWMA (span=5, last value)
      - std (pandas default, ddof=1)
    """
    df = df_all[df_all["Round"] <= up_to_round].copy()
    df["Min"] = df["Minutes"].apply(parse_minutes)
    df = df[df["Min"] > 0].copy()

    for raw_col in RAW_TO_40:
        df[raw_col] = df[raw_col].fillna(0)

    # Per-40 ανά ματς
    for raw_col, p40_col in RAW_TO_40.items():
        df[p40_col] = df[raw_col] / df["Min"] * 40.0

    df["PIR_per_40"] = df["Valuation"].astype(float) / df["Min"] * 40.0

    # Χρονολογική σειρά για EWMA
    df = df.sort_values(["Player_ID", "Gamecode"])

    grouped = df.groupby("Player_ID")

    stats = pd.DataFrame()
    stats["GP"] = grouped.size()
    stats["MPG"] = grouped["Min"].mean().round(1)

    for col in ALL_P40_COLS:
        # EWMA: τελευταία τιμή
        stats[f"{col}_ewma"] = grouped[col].apply(
            lambda x: x.ewm(span=EWMA_SPAN, adjust=False).mean().iloc[-1]
        ).round(4)
        # Mean: απλός μέσος
        stats[f"{col}_mean"] = grouped[col].mean().round(4)
        # Std: τυπική απόκλιση
        stats[f"{col}_std"] = grouped[col].std().fillna(0.0).round(4)

    stats = stats.reset_index()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Build anchor per round for backtest")
    parser.add_argument("--round", type=int, help="Φτιάχνει anchor μόνο για αυτή την αγωνιστική")
    parser.add_argument("--all", action="store_true", help="Φτιάχνει anchors για rounds 5..37 (RS backtest)")
    parser.add_argument("--from", type=int, dest="from_round", default=5, help="Αρχική αγωνιστική (default 5)")
    parser.add_argument("--to", type=int, dest="to_round", default=37, help="Τελική αγωνιστική (default 37)")
    args = parser.parse_args()

    if not os.path.exists(BOXSCORES_FILE):
        print(f"⚠ Δεν βρέθηκε: {BOXSCORES_FILE}")
        sys.exit(1)

    df_all = pd.read_csv(BOXSCORES_FILE)
    df_all["Player_ID"] = df_all["Player_ID"].str.strip()
    # Μόνο Regular Season
    df_all = df_all[df_all["Phase"] == "RS"].copy()
    max_round = int(df_all["Round"].max())
    print(f"→ Loaded boxscores (RS only): {len(df_all)} rows, rounds 1-{max_round}")

    os.makedirs(ANCHORS_DIR, exist_ok=True)

    # Ποια rounds να χτίσει
    if args.round:
        rounds_to_build = [args.round]
    elif args.all:
        rounds_to_build = list(range(args.from_round, args.to_round + 1))
    else:
        print("Χρήση: --round N  ή  --all  ή  --from X --to Y")
        sys.exit(1)

    for r in rounds_to_build:
        anchor = build_anchor(df_all, r)
        out_path = os.path.join(ANCHORS_DIR, f"anchor_round_{r}.csv")
        anchor.to_csv(out_path, index=False)
        print(f"  ✅ Round {r}: {len(anchor)} παίκτες → {out_path}")

    print(f"\n✅ Ολοκληρώθηκε! {len(rounds_to_build)} anchor αρχεία στο {ANCHORS_DIR}/")


if __name__ == "__main__":
    main()
