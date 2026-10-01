#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════════
 Script 2: build_projections_backtest.py
══════════════════════════════════════════════════════════════════
Για αγωνιστική N (≥6):
  1. Φορτώνει anchor_round_{N-1}.csv (χτισμένο από Script 1)
  2. Κοιτάει ποιοι έπαιξαν στο round N (boxscores)
  3. Για κάθε παίκτη:
     - Projected per-40 components = EWMA από anchor
     - Projected PIR per 40 = synthesize_pir(projected_components)
     - Projected per actual_minutes = component × (actual_min/40)
     - Projected PIR = PIR_per_40 × (actual_min/40)
     - Floor/Ceiling ανά component (std fallback: current→2024)
     - Actual per-40 components (από boxscore)
     - Actual PIR (από boxscore)

Output: test/projections/projection_round_{N}.csv

Χρήση:
  python build_projections_backtest.py --round 6         # μόνο round 6
  python build_projections_backtest.py --all              # rounds 6..max
  python build_projections_backtest.py --from 6 --to 38   # rounds 6..38
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np

# ── Config ─────────────────────────────────────────────────────
STD_FRACTION = 0.5
MIN_GP_STD = 3

# Per-component STD_FRACTION — noisy components get wider range
COMPONENT_STD_FRACTION = {
    'FG2M_40': 0.5, 'FG2A_40': 0.5, 'FG3M_40': 0.5, 'FG3A_40': 0.5,
    'FTM_40': 0.5, 'FTA_40': 0.5, 'REB_40': 0.5, 'AST_40': 0.5,
    'STL_40': 0.5, 'BLK_40': 0.5, 'TOV_40': 0.6,
    'FD_40': 0.5, 'FC_40': 0.7, 'BA_40': 0.7,
}

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ANCHORS_DIR = os.path.join(TEST_DIR, "anchors")
PROJECTIONS_DIR = os.path.join(TEST_DIR, "projections")
BOXSCORES_FILE = os.path.join(TEST_DIR, "boxscores_2025.csv")
ANCHOR_2024_FILE = os.path.join(TEST_DIR, "anchor_2024.csv")

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

PIR_SIGN = {
    'FG2M_40': +1, 'FG2A_40': -1,
    'FG3M_40': +1, 'FG3A_40': -1,
    'FTM_40':  +1, 'FTA_40':  -1,
    'REB_40':  +1, 'AST_40':  +1,
    'STL_40':  +1, 'BLK_40':  +1,
    'TOV_40':  -1,
    'FD_40':   +1, 'FC_40':   -1, 'BA_40': -1,
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


def synthesize_pir(stats):
    return sum(PIR_COEFF[comp] * stats.get(comp, 0.0) for comp in COMPONENT_STATS)


def get_stds(pid, anchor_current, anchor_2024):
    """Std fallback: current (GP≥3) → 2024 → zeros."""
    STD_COLS = [f"{s}_std" for s in COMPONENT_STATS]

    if anchor_current is not None:
        row = anchor_current[anchor_current["Player_ID"] == pid]
        if len(row) > 0 and row.iloc[0]["GP"] >= MIN_GP_STD:
            r = row.iloc[0]
            return {col: float(r[col]) if col in r.index and pd.notna(r[col]) else 0.0
                    for col in STD_COLS}

    if anchor_2024 is not None:
        row = anchor_2024[anchor_2024["Player_ID"] == pid]
        if len(row) > 0:
            r = row.iloc[0]
            return {col: float(r[col]) if col in r.index and pd.notna(r[col]) else 0.0
                    for col in STD_COLS}

    return {col: 0.0 for col in STD_COLS}


def compute_floor_ceiling(means, stds):
    floor_s, ceil_s = {}, {}
    for comp in COMPONENT_STATS:
        mean = means.get(comp, 0.0)
        std = stds.get(f"{comp}_std", 0.0)
        sign = PIR_SIGN[comp]
        std_frac = COMPONENT_STD_FRACTION.get(comp, STD_FRACTION)
        if sign > 0:
            floor_s[comp] = max(0.0, mean - std_frac * std)
            ceil_s[comp] = mean + std_frac * std
        else:
            floor_s[comp] = mean + std_frac * std
            ceil_s[comp] = max(0.0, mean - std_frac * std)
    # Ceiling cap: made δεν μπορεί να ξεπεράσει attempted
    for made, att in [('FG2M_40', 'FG2A_40'), ('FG3M_40', 'FG3A_40'), ('FTM_40', 'FTA_40')]:
        ceil_s[att] = max(ceil_s[att], ceil_s[made])

    return floor_s, ceil_s


def build_projection_for_round(pred_round, df_all, anchor_2024):
    """Builds projection file for one round."""
    anchor_path = os.path.join(ANCHORS_DIR, f"anchor_round_{pred_round - 1}.csv")
    if not os.path.exists(anchor_path):
        print(f"  ⚠ anchor_round_{pred_round - 1}.csv δεν βρέθηκε! Τρέξε πρώτα Script 1.")
        return None

    anchor = pd.read_csv(anchor_path)
    anchor["Player_ID"] = anchor["Player_ID"].str.strip()

    # Παίκτες που έπαιξαν στο round
    round_games = df_all[
        (df_all["Round"] == pred_round) &
        (df_all["Min_parsed"] > 0)
    ].copy()

    if len(round_games) == 0:
        return None

    rows = []
    for _, game_row in round_games.iterrows():
        pid = game_row["Player_ID"]
        actual_minutes = game_row["Min_parsed"]
        min_factor = actual_minutes / 40.0

        # Actual per-40 components
        actual_p40 = {}
        for raw_col, p40_col in RAW_TO_40.items():
            actual_p40[p40_col] = game_row[raw_col] / actual_minutes * 40.0

        actual_pir = float(game_row["Valuation"])
        actual_pir_per_40 = actual_pir / actual_minutes * 40.0

        # Predicted from anchor
        anchor_row = anchor[anchor["Player_ID"] == pid]
        if len(anchor_row) == 0:
            continue

        ar = anchor_row.iloc[0]
        player_gp = int(ar["GP"])

        # EWMA values as predictions
        predicted_p40 = {}
        for comp in COMPONENT_STATS:
            ewma_col = f"{comp}_ewma"
            predicted_p40[comp] = float(ar[ewma_col]) if ewma_col in ar.index and pd.notna(ar[ewma_col]) else 0.0

        predicted_pir_per_40 = synthesize_pir(predicted_p40)
        predicted_pir = predicted_pir_per_40 * min_factor

        # Stds + Floor/Ceiling
        player_stds = get_stds(pid, anchor, anchor_2024)
        floor_stats, ceil_stats = compute_floor_ceiling(predicted_p40, player_stds)
        floor_pir_per_40 = synthesize_pir(floor_stats)
        ceil_pir_per_40 = synthesize_pir(ceil_stats)
        floor_pir = floor_pir_per_40 * min_factor
        ceil_pir = ceil_pir_per_40 * min_factor

        # Build row
        result = {
            "Player_ID": pid,
            "Player": game_row["Player"],
            "Team": game_row["Team"],
            "Round": pred_round,
            "Gamecode": game_row["Gamecode"],
            "GP_before": player_gp,
            "Actual_Minutes": round(actual_minutes, 1),
        }

        # Per-component: predicted per 40, predicted per actual_min, actual per 40, actual per actual_min
        for comp in COMPONENT_STATS:
            result[f"pred_{comp}_per40"] = round(predicted_p40[comp], 4)
            result[f"pred_{comp}_game"] = round(predicted_p40[comp] * min_factor, 4)
            result[f"actual_{comp}_per40"] = round(actual_p40[comp], 4)
            result[f"actual_{comp}_game"] = round(actual_p40[comp] * min_factor, 4)
            result[f"error_{comp}_per40"] = round(actual_p40[comp] - predicted_p40[comp], 4)
            result[f"error_{comp}_game"] = round(actual_p40[comp] * min_factor - predicted_p40[comp] * min_factor, 4)
            result[f"std_{comp}"] = round(player_stds.get(f"{comp}_std", 0.0), 4)

        # PIR summary
        result["Predicted_PIR_per_40"] = round(predicted_pir_per_40, 2)
        result["Predicted_PIR"] = round(predicted_pir, 2)
        result["Actual_PIR_per_40"] = round(actual_pir_per_40, 2)
        result["Actual_PIR"] = actual_pir
        result["Floor_PIR"] = round(floor_pir, 2)
        result["Ceiling_PIR"] = round(ceil_pir, 2)
        result["Floor_PIR_per_40"] = round(floor_pir_per_40, 2)
        result["Ceiling_PIR_per_40"] = round(ceil_pir_per_40, 2)
        result["Error_PIR"] = round(actual_pir - predicted_pir, 2)
        result["Error_PIR_per_40"] = round(actual_pir_per_40 - predicted_pir_per_40, 2)
        result["In_Range"] = 1 if floor_pir <= actual_pir <= ceil_pir else 0

        rows.append(result)

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Build projections per round for backtest")
    parser.add_argument("--round", type=int, help="Projection μόνο για αυτή την αγωνιστική")
    parser.add_argument("--all", action="store_true", help="Projections για rounds 6..38 (RS backtest)")
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

    # Load 2024 anchor for std fallback
    anchor_2024 = None
    if os.path.exists(ANCHOR_2024_FILE):
        anchor_2024 = pd.read_csv(ANCHOR_2024_FILE)
        anchor_2024["Player_ID"] = anchor_2024["Player_ID"].str.strip()
        print(f"→ Loaded anchor_2024: {len(anchor_2024)} players (std fallback)")

    os.makedirs(PROJECTIONS_DIR, exist_ok=True)

    # Ποια rounds
    if args.round:
        rounds = [args.round]
    elif args.all:
        rounds = list(range(args.from_round, args.to_round + 1))
    else:
        print("Χρήση: --round N  ή  --all  ή  --from X --to Y")
        sys.exit(1)

    for r in rounds:
        df_proj = build_projection_for_round(r, df_all, anchor_2024)
        if df_proj is not None and len(df_proj) > 0:
            out_path = os.path.join(PROJECTIONS_DIR, f"projection_round_{r}.csv")
            df_proj.to_csv(out_path, index=False)
            print(f"  ✅ Round {r}: {len(df_proj)} παίκτες → {out_path}")

    print(f"\n✅ Ολοκληρώθηκε! Projection αρχεία στο {PROJECTIONS_DIR}/")


if __name__ == "__main__":
    main()
