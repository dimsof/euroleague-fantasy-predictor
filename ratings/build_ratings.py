#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════
 build_ratings.py  —  Υπολογισμός ratings ανά παίκτη
════════════════════════════════════════════════════════════════════
Παράγει: data/ratings.csv

Λογική (component-based + EWMA):
  1. Φορτώνει players_stats.xlsx (βάση παικτών)
  2. Φορτώνει anchors (2024, 2025, 2026) — κάθε anchor έχει per-40 stats
     Anchor 2026 χρησιμοποιεί EWMA (span=5) αντί για simple mean.
  3. Φορτώνει projected_minutes.csv
  4. Για κάθε παίκτη:
     - Αν GP_2026 >= 5 → χρήση EWMA stats απευθείας (χωρίς blending)
     - Αν GP_2026 < 5  → fallback σε παλιές σεζόν
  5. Σύνθεση PIR από components (πλήρης — χωρίς residual):
       PIR = 3×FG2M - FG2A + 4×FG3M - FG3A + 2×FTM - FTA
             + REB + AST + STL + BLK - TOV + FD - FC - BA
  6. Projected_PIR = synthesized_PIR_per_40 × (Projected_Minutes / 40)
  7. Floor / Ceiling ανά component με per-player std (fallback: 2026→2025→2024)
  8. Value στήλες (per credit)

Fallback (GP_2026 < 5 → χωρίς 2026 data):
  - Κανένας           → zeros
  - Μόνο 2024         → stats_2024
  - Μόνο 2025         → stats_2025
  - 2024+2025         → stats_2025 (πιο πρόσφατο)
"""

import os
import sys
import pandas as pd
import numpy as np

# data/ βρίσκεται στο app/data/ → δύο επίπεδα πάνω από ratings/
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# ── Config ──────────────────────────────────────────────────────
STD_FRACTION = 0.5        # πόσο της std χρησιμοποιούμε για floor/ceiling
MIN_GP = 1               # ελάχιστα GP για να θεωρηθεί valid ένα anchor (means)
MIN_GP_STD = 3            # ελάχιστα GP για αξιόπιστο std — αλλιώς fallback σε παλιά σεζόν

# Per-component STD_FRACTION — noisy components get wider range
COMPONENT_STD_FRACTION = {
    'FG2M_40': 0.5, 'FG2A_40': 0.5, 'FG3M_40': 0.5, 'FG3A_40': 0.5,
    'FTM_40': 0.5, 'FTA_40': 0.5, 'REB_40': 0.5, 'AST_40': 0.5,
    'STL_40': 0.5, 'BLK_40': 0.5, 'TOV_40': 0.6,
    'FD_40': 0.5, 'FC_40': 0.7, 'BA_40': 0.7,
}

# Πρόσημο κάθε component στον τύπο PIR
# +1 = θετική συνεισφορά (περισσότερο = καλύτερο PIR)
# -1 = αρνητική συνεισφορά (περισσότερο = χειρότερο PIR)
PIR_SIGN = {
    'FG2M_40': +1, 'FG2A_40': -1,
    'FG3M_40': +1, 'FG3A_40': -1,
    'FTM_40':  +1, 'FTA_40':  -1,
    'REB_40':  +1, 'AST_40':  +1,
    'STL_40':  +1, 'BLK_40':  +1,
    'TOV_40':  -1,
    'FD_40':   +1, 'FC_40':   -1, 'BA_40': -1,
}

# ── Component stats που κουβαλάμε στο pipeline ──────────────────
COMPONENT_STATS = [
    'FG2M_40', 'FG2A_40', 'FG3M_40', 'FG3A_40',
    'FTM_40', 'FTA_40', 'REB_40', 'AST_40',
    'STL_40', 'BLK_40', 'TOV_40',
    'FD_40', 'FC_40', 'BA_40',   # fouls drawn, fouls committed, blocks against
]

# Columns που διαβάζουμε από τα anchors: per-40 mean + std + PIR_std
ANCHOR_COLS = []
for _s in COMPONENT_STATS:
    ANCHOR_COLS.append(_s)
    ANCHOR_COLS.append(f"{_s}_std")
ANCHOR_COLS.append('PIR_per_40_std')


def zero_stats():
    """Dict με μηδενικά για όλα τα stats."""
    return {col: 0.0 for col in ANCHOR_COLS}


def synthesize_pir(stats):
    """
    Υπολογίζει PIR_per_40 από τα component stats (πλήρης τύπος).
    PIR = 3×FG2M - FG2A + 4×FG3M - FG3A + 2×FTM - FTA
          + REB + AST + STL + BLK - TOV + FD - FC - BA
    """
    return (3 * stats['FG2M_40'] - stats['FG2A_40']
            + 4 * stats['FG3M_40'] - stats['FG3A_40']
            + 2 * stats['FTM_40'] - stats['FTA_40']
            + stats['REB_40'] + stats['AST_40']
            + stats['STL_40'] + stats['BLK_40']
            - stats['TOV_40']
            + stats['FD_40'] - stats['FC_40'] - stats['BA_40'])


def _component_floor_ceiling(means, stds):
    """
    Υπολογίζει per-40 stats για Floor και Ceiling ξεχωριστά ανά component.
    Χρησιμοποιεί τη δική του κάθε παίκτη std ανά component.

    Floor  → χειρότερο σενάριο:
      θετικά PIR components (FG2M, REB...): mean - Z×std  (λιγότερα = χειρότερα)
      αρνητικά PIR components (FG2A, TOV...): mean + Z×std  (περισσότερα = χειρότερα)

    Ceiling → καλύτερο σενάριο:
      θετικά PIR components: mean + Z×std  (περισσότερα = καλύτερα)
      αρνητικά PIR components: mean - Z×std  (λιγότερα = καλύτερα)

    Clamp στο 0 (δεν γίνεται αρνητικό stat).

    Args:
        means: dict με per-40 mean stats του παίκτη
        stds:  dict με per-40 std stats του παίκτη (μπορεί να είναι από άλλη σεζόν)

    Returns: (floor_stats_dict, ceiling_stats_dict)
    """
    floor_stats = {}
    ceil_stats = {}
    for comp in COMPONENT_STATS:
        mean = means[comp]
        std = stds.get(f"{comp}_std", 0.0)
        sign = PIR_SIGN[comp]

        std_frac = COMPONENT_STD_FRACTION.get(comp, STD_FRACTION)
        if sign > 0:
            floor_stats[comp] = max(0.0, mean - std_frac * std)
            ceil_stats[comp] = mean + std_frac * std
        else:
            floor_stats[comp] = mean + std_frac * std
            ceil_stats[comp] = max(0.0, mean - std_frac * std)

    # Ceiling cap: made δεν μπορεί να ξεπεράσει attempted
    for made, att in [('FG2M_40', 'FG2A_40'), ('FG3M_40', 'FG3A_40'), ('FTM_40', 'FTA_40')]:
        ceil_stats[att] = max(ceil_stats[att], ceil_stats[made])

    return floor_stats, ceil_stats


def get_player_stds(pid, anchor_2026, anchor_2025, anchor_2024,
                    is_2026, is_2025, is_2024):
    """
    Βρίσκει τα per-component stds ενός παίκτη.
    Fallback: 2026 (αν GP>=3) → 2025 → 2024 → zeros.
    """
    STD_COLS = [f"{s}_std" for s in COMPONENT_STATS]

    def _extract_stds(anchor_df, min_gp):
        row = anchor_df[anchor_df["Player_ID"] == pid]
        if len(row) == 0:
            return None
        r = row.iloc[0]
        if r["GP"] < min_gp:
            return None
        return {col: float(r[col]) if col in r.index and pd.notna(r[col]) else 0.0
                for col in STD_COLS}

    # 2026 αν GP >= 3
    if is_2026:
        stds = _extract_stds(anchor_2026, MIN_GP_STD)
        if stds is not None:
            return stds

    # Fallback 2025
    if is_2025:
        stds = _extract_stds(anchor_2025, 1)
        if stds is not None:
            return stds

    # Fallback 2024
    if is_2024:
        stds = _extract_stds(anchor_2024, 1)
        if stds is not None:
            return stds

    # Τίποτα → zeros
    return {col: 0.0 for col in STD_COLS}


def load_anchor(year):
    """Φορτώνει anchor αρχείο αν υπάρχει."""
    path = os.path.join(DATA_DIR, f"anchor_{year}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        df["Player_ID"] = df["Player_ID"].str.strip()
        return df, True
    return None, False


def get_anchor_data(pid, anchor_df, is_here):
    """
    Επιστρέφει (GP, stats_dict) ενός παίκτη από ένα anchor.
    stats_dict περιέχει όλα τα per-40 stats + stds.
    """
    if not is_here:
        return None, None
    row = anchor_df[anchor_df["Player_ID"] == pid]
    if len(row) == 0:
        return None, None
    r = row.iloc[0]
    gp = r["GP"]
    if gp < MIN_GP:
        return None, None
    stats = {}
    for col in ANCHOR_COLS:
        if col in r.index and pd.notna(r[col]):
            stats[col] = float(r[col])
        else:
            stats[col] = 0.0
    return gp, stats


def compute_weighted_stats(pid, anchor_2024, anchor_2025, anchor_2026,
                           is_2024, is_2025, is_2026):
    """
    Υπολογίζει per-40 stats για έναν παίκτη.
    - Αν GP_2026 >= MIN_GP → EWMA stats απευθείας (χωρίς blending)
    - Αν GP_2026 < MIN_GP  → fallback σε παλιές σεζόν
    Επιστρέφει (stats_dict, source_description).
    """
    gp24, stats24 = get_anchor_data(pid, anchor_2024, is_2024)
    gp25, stats25 = get_anchor_data(pid, anchor_2025, is_2025)
    gp26, stats26 = get_anchor_data(pid, anchor_2026, is_2026)

    has_24 = gp24 is not None
    has_25 = gp25 is not None
    has_26 = gp26 is not None

    # ── EWMA: αν έχει >=5 ματς φέτος, χρήση EWMA απευθείας ──
    if has_26:
        return stats26, "ewma_2026"

    # ── Fallback: χωρίς αρκετά ματς φέτος → παλιές σεζόν ──
    if not has_24 and not has_25:
        return zero_stats(), "none"

    if has_24 and not has_25:
        return stats24, "2024_only"

    if not has_24 and has_25:
        return stats25, "2025_only"

    # has_24 and has_25 → πιο πρόσφατο (2025)
    return stats25, "2024+2025"


def build_ratings():
    """Κύρια συνάρτηση: υπολογίζει ratings για κάθε παίκτη."""

    # ── 1. Φόρτωση players_stats ──
    ps_path = os.path.join(DATA_DIR, "players_stats.xlsx")
    ps = pd.read_excel(ps_path)
    ps = ps[ps["Name"].notna() & ps["Surname"].notna()].copy()
    ps = ps[ps["Position"].isin(["Guard", "Forward", "Center"])].copy()
    ps = ps[pd.to_numeric(ps["ID"], errors="coerce").notna()].copy()
    ps["Player_ID"] = "P" + ps["ID"].astype(int).astype(str).str.zfill(6)
    ps["Player"] = ps["Name"] + " " + ps["Surname"]

    print(f"→ {len(ps)} παίκτες από players_stats.xlsx")

    # ── 2. Φόρτωση anchors ──
    anchor_2024, is_2024 = load_anchor(2024)
    anchor_2025, is_2025 = load_anchor(2025)
    anchor_2026, is_2026 = load_anchor(2026)

    found = []
    if is_2024: found.append("2024")
    if is_2025: found.append("2025")
    if is_2026: found.append("2026")
    print(f"→ Anchors: {', '.join(found) if found else 'ΚΑΝΕΝΑ'}")

    # ── 3. Φόρτωση projected minutes ──
    pm_path = os.path.join(DATA_DIR, "projected_minutes.csv")
    pm = pd.read_csv(pm_path)
    pm["Player_ID"] = pm["Player_ID"].str.strip()
    print(f"→ Projected minutes: {len(pm)} παίκτες")

    # ── 4. Υπολογισμός weighted stats ──
    results = []
    for _, row in ps.iterrows():
        pid = row["Player_ID"]
        credits = row["Quotation"]

        weighted_stats, source = compute_weighted_stats(
            pid, anchor_2024, anchor_2025, anchor_2026,
            is_2024, is_2025, is_2026
        )

        # Σύνθεση PIR από components
        pir_per_40 = synthesize_pir(weighted_stats)

        # Per-player stds (fallback: 2026→2025→2024)
        player_stds = get_player_stds(
            pid, anchor_2026, anchor_2025, anchor_2024,
            is_2026, is_2025, is_2024
        )

        # Floor/Ceiling ανά component με τη δική του κάθε παίκτη std
        floor_stats, ceil_stats = _component_floor_ceiling(weighted_stats, player_stds)
        floor_pir_40 = synthesize_pir(floor_stats)
        ceil_pir_40 = synthesize_pir(ceil_stats)

        # Projected minutes
        pm_row = pm[pm["Player_ID"] == pid]
        proj_min = pm_row.iloc[0]["Projected_Minutes"] if len(pm_row) else 0.0

        # Final PIR = per_40 × (minutes / 40)
        min_factor = proj_min / 40.0
        projected_pir = pir_per_40 * min_factor
        floor_pir = floor_pir_40 * min_factor
        ceiling_pir = ceil_pir_40 * min_factor

        results.append({
            "Player_ID": pid,
            "Player": row["Player"],
            "Team": row["Team"],
            "Position": row["Position"],
            "Credits": credits,
            "Projected_Minutes": proj_min,
            "PIR_per_40": round(pir_per_40, 1),
            "Projected_PIR": round(projected_pir, 1),
            "Floor": round(floor_pir, 1),
            "Ceiling": round(ceiling_pir, 1),
            "Source": source,
        })

    df = pd.DataFrame(results)

    # Summary sources
    sources = df["Source"].value_counts().to_dict()
    print(f"→ Sources: {sources}")

    # ── 5. Value στήλες (per credit) ──
    df["Value_Avg"] = (df["Projected_PIR"] / df["Credits"].replace(0, pd.NA)).round(2)
    df["Value_Floor"] = (df["Floor"] / df["Credits"].replace(0, pd.NA)).round(2)
    df["Value_Ceiling"] = (df["Ceiling"] / df["Credits"].replace(0, pd.NA)).round(2)

    # ── 6. Output ──
    df = df.sort_values("Value_Avg", ascending=False)

    out_path = os.path.join(DATA_DIR, "ratings.csv")
    df.to_csv(out_path, index=False)

    print(f"\n✅ {out_path}: {len(df)} παίκτες")
    print(f"\nTop 20:")
    print(f"{'Player':<25} {'Team':<5} {'Pos':<4} {'Cr':>3} {'PM':>5} {'PIR40':>6} {'ProjPIR':>8} {'Floor':>6} {'Ceil':>6} {'Val':>5}")
    print("-" * 78)
    for _, r in df.head(20).iterrows():
        print(f"{r['Player']:<25} {r['Team']:<5} {r['Position']:<4} {r['Credits']:>3.0f} "
              f"{r['Projected_Minutes']:>5.1f} {r['PIR_per_40']:>6.1f} "
              f"{r['Projected_PIR']:>8.1f} {r['Floor']:>6.1f} {r['Ceiling']:>6.1f} "
              f"{r['Value_Avg']:>5.2f}")

    return df


main = build_ratings

if __name__ == "__main__":
    build_ratings()
