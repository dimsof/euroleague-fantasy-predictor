#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════════
 Script 4: build_actuals_backtest.py
══════════════════════════════════════════════════════════════════
Για κάθε αγωνιστική (6..38), φτιάχνει αρχείο με τα ΠΡΑΓΜΑΤΙΚΑ
PIR components του κάθε παίκτη (per 40 min + per actual minutes).

Output: test/actuals/actual_round_{N}.csv

Χρήση:
  python build_actuals_backtest.py --all              # rounds 6..38
  python build_actuals_backtest.py --round 10         # μόνο round 10
  python build_actuals_backtest.py --from 6 --to 20   # rounds 6..20
"""

import os
import sys
import argparse
import pandas as pd

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUALS_DIR = os.path.join(TEST_DIR, "actuals")
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

PIR_COEFF = {
    'FG2M_40': 3, 'FG2A_40': -1,
    'FG3M_40': 4, 'FG3A_40': -1,
    'FTM_40':  2, 'FTA_40':  -1,
    'REB_40':  1, 'AST_40':  1,
    'STL_40':  1, 'BLK_40':  1,
    'TOV_40':  -1,
    'FD_40':   1, 'FC_40':   -1, 'BA_40': -1,
}


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


def build_actuals_for_round(rd, df_all):
    """Φτιάχνει actual stats αρχείο για μία αγωνιστική."""
    round_games = df_all[
        (df_all["Round"] == rd) &
        (df_all["Min_parsed"] > 0)
    ].copy()

    if len(round_games) == 0:
        return None

    rows = []
    for _, game_row in round_games.iterrows():
        actual_minutes = game_row["Min_parsed"]
        min_factor = actual_minutes / 40.0

        result = {
            "Player_ID": game_row["Player_ID"],
            "Player": game_row["Player"],
            "Team": game_row["Team"],
            "Round": rd,
            "Gamecode": game_row["Gamecode"],
            "Actual_Minutes": round(actual_minutes, 1),
        }

        # Per-40 και per-game actual components
        for raw_col, p40_col in RAW_TO_40.items():
            raw_val = game_row[raw_col]
            per40 = raw_val / actual_minutes * 40.0
            result[f"{p40_col}_per40"] = round(per40, 4)
            result[f"{p40_col}_game"] = round(raw_val, 4)

        # Actual PIR
        actual_pir = float(game_row["Valuation"])
        result["Actual_PIR"] = actual_pir
        result["Actual_PIR_per_40"] = round(actual_pir / actual_minutes * 40.0, 2)

        rows.append(result)

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Build actual stats per round for backtest")
    parser.add_argument("--round", type=int, help="Μόνο για αυτή την αγωνιστική")
    parser.add_argument("--all", action="store_true", help="Rounds 6..38 (RS)")
    parser.add_argument("--from", type=int, dest="from_round", default=6, help="Αρχική αγωνιστική (default 6)")
    parser.add_argument("--to", type=int, dest="to_round", default=38, help="Τελική αγωνιστική (default 38)")
    args = parser.parse_args()

    if not os.path.exists(BOXSCORES_FILE):
        print(f"⚠ Δεν βρέθηκε: {BOXSCORES_FILE}")
        sys.exit(1)

    df_all = pd.read_csv(BOXSCORES_FILE)
    df_all["Player_ID"] = df_all["Player_ID"].str.strip()
    # Μόνο Regular Season
    df_all = df_all[df_all["Phase"] == "RS"].copy()
    df_all["Min_parsed"] = df_all["Minutes"].apply(parse_minutes)
    for raw_col in RAW_TO_40:
        df_all[raw_col] = df_all[raw_col].fillna(0)

    max_round = int(df_all["Round"].max())
    print(f"→ Loaded boxscores (RS only): {len(df_all)} rows, rounds 1-{max_round}")

    os.makedirs(ACTUALS_DIR, exist_ok=True)

    # Ποια rounds
    if args.round:
        rounds = [args.round]
    elif args.all:
        rounds = list(range(args.from_round, args.to_round + 1))
    else:
        print("Χρήση: --round N  ή  --all  ή  --from X --to Y")
        sys.exit(1)

    for r in rounds:
        df_act = build_actuals_for_round(r, df_all)
        if df_act is not None and len(df_act) > 0:
            out_path = os.path.join(ACTUALS_DIR, f"actual_round_{r}.csv")
            df_act.to_csv(out_path, index=False)
            print(f"  ✅ Round {r}: {len(df_act)} παίκτες → {out_path}")

    print(f"\n✅ Ολοκληρώθηκε! Actual αρχεία στο {ACTUALS_DIR}/")


if __name__ == "__main__":
    main()
