#!/usr/bin/env python3
"""
Φτιάχνει τα anchor αρχεία (aggregated per-player stats) από τα boxscores.
Παράγει: data/anchor_{year}.csv

Per-40 component stats:
  Κάθε στατιστικό υπολογίζεται per-40-minutes ΑΝΑ ΜΑΤΣ, και μετά
  παίρνουμε mean + std ανά παίκτη. Έτσι το build_ratings.py μπορεί
  να κάνει component-based πρόβλεψη αντί για σκέτο PIR.

EWMA mode (τρέχουσα σεζόν):
  Αντί για simple mean, χρησιμοποιεί EWMA (span=5) ανά παίκτη
  ταξινομημένο χρονολογικά (Gamecode). Κρατάει την τελευταία τιμή
  EWMA ως "τρέχον επίπεδο". Std παραμένει η κανονική standard deviation.
  Παλιές σεζόν → simple mean (χωρίς EWMA).

Σύνθεση PIR (πλήρης — χωρίς residual):
  PIR = 3×FG2M - FG2A + 4×FG3M - FG3A + 2×FTM - FTA
        + REB + AST + STL + BLK - TOV
        + FD (FoulsReceived) - FC (FoulsCommited) - BA (BlocksAgainst)
"""

import pandas as pd
import os

# data/ βρίσκεται στο app/data/ → δύο επίπεδα πάνω από ratings/
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

BOXSCORES = {
    2026: os.path.join(DATA_DIR, "boxscores_2026.csv"),
    2025: os.path.join(DATA_DIR, "boxscores_2025.csv"),
    2024: os.path.join(DATA_DIR, "OLD", "boxscores_2024.csv"),
}

CURRENT_SEASON = 2026
EWMA_SPAN = 5   # span for EWMA — last game gets ~33% weight

# ── Αντιστοίχιση raw column → per-40 column ────────────────────────
RAW_TO_40 = {
    'Points':               'PTS_40',
    'TotalRebounds':        'REB_40',
    'Assistances':          'AST_40',
    'Steals':               'STL_40',
    'BlocksFavour':         'BLK_40',
    'Turnovers':            'TOV_40',
    'FieldGoalsMade2':      'FG2M_40',
    'FieldGoalsAttempted2': 'FG2A_40',
    'FieldGoalsMade3':      'FG3M_40',
    'FieldGoalsAttempted3': 'FG3A_40',
    'FreeThrowsMade':       'FTM_40',
    'FreeThrowsAttempted':  'FTA_40',
    'FoulsReceived':        'FD_40',
    'FoulsCommited':        'FC_40',     # ένα 't' — έτσι το γράφει το API
    'BlocksAgainst':        'BA_40',
}

# Όλα τα per-40 columns (including PIR)
PER40_COLS = list(RAW_TO_40.values()) + ['PIR_per_40']


def parse_minutes(val):
    """Μετατρέπει '33:21' ή αριθμό σε δεκαδικά λεπτά."""
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


def build_anchor(box_file, output_file, use_ewma=False, ewma_span=EWMA_SPAN):
    """Διαβάζει boxscores, υπολογίζει per-40 stats + std ανά παίκτη.
    Αν use_ewma=True, χρησιμοποιεί EWMA αντί για simple mean."""
    df = pd.read_csv(box_file)

    # Μόνο παίκτες που έπαιξαν (με λεπτά > 0)
    df["Min"] = df["Minutes"].apply(parse_minutes)
    df = df[df["Min"] > 0].copy()
    df["PIR"] = df["Valuation"].astype(float)
    df["Player_ID"] = df["Player_ID"].str.strip()

    # Fillna στα raw stats (αν κάποιο λείπει)
    for raw_col in RAW_TO_40:
        df[raw_col] = df[raw_col].fillna(0)

    # ── Per-40 stats ανά ματς ──────────────────────────────────────
    for raw_col, p40_col in RAW_TO_40.items():
        df[p40_col] = df[raw_col] / df["Min"] * 40.0

    df["PIR_per_40"] = df["PIR"] / df["Min"] * 40.0

    # Χρονολογική ταξινόμηση για EWMA
    if use_ewma:
        df = df.sort_values(["Player_ID", "Gamecode"])

    # ── Aggregation ανά παίκτη ─────────────────────────────────────
    grouped = df.groupby("Player_ID")

    stats = pd.DataFrame()
    stats["GP"] = grouped.size()
    stats["MPG"] = grouped["Min"].mean().round(1)

    # Per-game stats (backward compatible)
    stats["PPG"] = grouped["Points"].mean().round(1)

    stats["FG2M"] = grouped["FieldGoalsMade2"].mean().round(1)
    fg2_made = grouped["FieldGoalsMade2"].sum()
    fg2_att = grouped["FieldGoalsAttempted2"].sum()
    stats["FG2_pct"] = ((fg2_made / fg2_att).where(fg2_att > 0, None) * 100).round(1)

    stats["TPM"] = grouped["FieldGoalsMade3"].mean().round(1)
    fg3_made = grouped["FieldGoalsMade3"].sum()
    fg3_att = grouped["FieldGoalsAttempted3"].sum()
    stats["TP_pct"] = ((fg3_made / fg3_att).where(fg3_att > 0, None) * 100).round(1)

    stats["FTM"] = grouped["FreeThrowsMade"].mean().round(1)
    ft_made = grouped["FreeThrowsMade"].sum()
    ft_att = grouped["FreeThrowsAttempted"].sum()
    stats["FT_pct"] = ((ft_made / ft_att).where(ft_att > 0, None) * 100).round(1)

    stats["RPG"] = grouped["TotalRebounds"].mean().round(1)
    stats["APG"] = grouped["Assistances"].mean().round(1)
    stats["SPG"] = grouped["Steals"].mean().round(1)
    stats["TPG"] = grouped["Turnovers"].mean().round(1)
    stats["BPG"] = grouped["BlocksFavour"].mean().round(1)

    # ── Per-40 stats: mean + std (ή EWMA + std) ────────────────────
    if use_ewma:
        for col in PER40_COLS:
            # EWMA: τελευταία τιμή της εκθετικά σταθμισμένης σειράς
            stats[col] = grouped[col].apply(
                lambda x: x.ewm(span=ewma_span, adjust=False).mean().iloc[-1]
            ).round(2)
            # Std: κανονική std για μεταβλητότητα
            stats[f"{col}_std"] = grouped[col].std().fillna(0.0).round(2)
    else:
        for col in PER40_COLS:
            stats[col] = grouped[col].mean().round(2)
            stats[f"{col}_std"] = grouped[col].std().fillna(0.0).round(2)

    stats = stats.reset_index()
    stats.to_csv(output_file, index=False)
    mode = "EWMA" if use_ewma else "mean"
    print(f"✅ {output_file}: {len(stats)} παίκτες, {len(PER40_COLS)} per-40 components ({mode})")
    return stats


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=CURRENT_SEASON,
                   help=f"ποια σεζόν να χτίσει (default {CURRENT_SEASON})")
    args = p.parse_args()

    season = args.season
    if season not in BOXSCORES:
        print(f"⚠ Δεν ξέρω σεζόν {season}")
    else:
        box_file = BOXSCORES[season]
        out = os.path.join(DATA_DIR, f"anchor_{season}.csv")
        if not os.path.exists(box_file):
            print(f"⚠ Δεν βρήκα {box_file}")
        else:
            build_anchor(box_file, out, use_ewma=(season == CURRENT_SEASON))
