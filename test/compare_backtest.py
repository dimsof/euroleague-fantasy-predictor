#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════════
 Script 3: compare_backtest.py
══════════════════════════════════════════════════════════════════
Φορτώνει τα projection αρχεία (projections/) και τα actual αρχεία
(actuals/), τα κάνει merge ανά Player_ID + Round, και συγκρίνει
projected vs actual PIR components στα ΠΡΑΓΜΑΤΙΚΑ ΛΕΠΤΑ.

1. Per-component analysis (per game):
   - MAE, RMSE, bias ανά component
   - PIR impact ανά component

2. PIR analysis:
   - MAE, RMSE, bias (per game & per 40)
   - Floor/Ceiling hit rate (συνολικό & per GP bucket)

3. Per-player breakdown:
   - MAE, hit_rate ανά παίκτη

4. Per-player per-component volatility

Output:
  test/results/summary.txt              — συνοπτικά στατιστικά
  test/results/per_component.csv        — MAE/RMSE/bias ανά component
  test/results/per_player.csv           — MAE/hit_rate ανά παίκτη
  test/results/per_player_component.csv — volatility ανά παίκτη ανά component
  test/results/all_comparisons.csv      — όλες οι συγκρίσεις μαζί

Χρήση:
  python compare_backtest.py
"""

import os
import sys
import pandas as pd
import numpy as np

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTIONS_DIR = os.path.join(TEST_DIR, "projections")
ACTUALS_DIR = os.path.join(TEST_DIR, "actuals")
RESULTS_DIR = os.path.join(TEST_DIR, "results")

COMPONENT_STATS = [
    'FG2M_40', 'FG2A_40', 'FG3M_40', 'FG3A_40',
    'FTM_40', 'FTA_40', 'REB_40', 'AST_40',
    'STL_40', 'BLK_40', 'TOV_40',
    'FD_40', 'FC_40', 'BA_40',
]

PIR_COEFF = {
    'FG2M_40': 3, 'FG2A_40': -1,
    'FG3M_40': 4, 'FG3A_40': -1,
    'FTM_40':  2, 'FTA_40':  -1,
    'REB_40':  1, 'AST_40':  1,
    'STL_40':  1, 'BLK_40':  1,
    'TOV_40':  -1,
    'FD_40':   1, 'FC_40':   -1, 'BA_40': -1,
}


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # ── Φόρτωση projection αρχείων ────────────────────────────
    proj_files = sorted([
        f for f in os.listdir(PROJECTIONS_DIR)
        if f.startswith("projection_round_") and f.endswith(".csv")
    ])
    if not proj_files:
        print("⚠ Δεν βρέθηκαν projection αρχεία! Τρέξε πρώτα Script 2.")
        sys.exit(1)

    proj_dfs = []
    for f in proj_files:
        proj_dfs.append(pd.read_csv(os.path.join(PROJECTIONS_DIR, f)))
    df_proj = pd.concat(proj_dfs, ignore_index=True)
    print(f"→ Projections: {len(df_proj)} rows, rounds {df_proj['Round'].min()}-{df_proj['Round'].max()}")

    # ── Φόρτωση actual αρχείων ────────────────────────────────
    act_files = sorted([
        f for f in os.listdir(ACTUALS_DIR)
        if f.startswith("actual_round_") and f.endswith(".csv")
    ])
    if not act_files:
        print("⚠ Δεν βρέθηκαν actual αρχεία! Τρέξε πρώτα Script 4.")
        sys.exit(1)

    act_dfs = []
    for f in act_files:
        act_dfs.append(pd.read_csv(os.path.join(ACTUALS_DIR, f)))
    df_act = pd.concat(act_dfs, ignore_index=True)
    print(f"→ Actuals: {len(df_act)} rows, rounds {df_act['Round'].min()}-{df_act['Round'].max()}")

    # ── Merge projected vs actual ─────────────────────────────
    # Projection columns: pred_{comp}_per40, pred_{comp}_game, std_{comp},
    #   Predicted_PIR, Predicted_PIR_per_40, Floor_PIR, Ceiling_PIR, GP_before
    # Actual columns: {comp}_per40, {comp}_game, Actual_PIR, Actual_PIR_per_40

    df = pd.merge(
        df_proj,
        df_act,
        on=["Player_ID", "Round", "Gamecode"],
        suffixes=("_proj", "_act"),
        how="inner"
    )
    print(f"→ Merged: {len(df)} comparisons, {df['Player_ID'].nunique()} παίκτες")

    # Χρησιμοποιούμε Player/Team/Actual_Minutes από τα actuals
    df["Player"] = df["Player_act"]
    df["Team"] = df["Team_act"]
    df["Actual_Minutes"] = df["Actual_Minutes_act"]

    # ── Υπολογισμός errors per component (per game) ───────────
    # Actual columns from actuals file: {comp}_game, {comp}_per40
    # Predicted columns from projections file: pred_{comp}_game, pred_{comp}_per40
    for comp in COMPONENT_STATS:
        # Error = actual - predicted (στα πραγματικά λεπτά)
        df[f"err_{comp}_game"] = df[f"{comp}_game"] - df[f"pred_{comp}_game"]
        # Error per 40
        df[f"err_{comp}_per40"] = df[f"{comp}_per40"] - df[f"pred_{comp}_per40"]

    # PIR errors (Actual_PIR_act from actuals, Predicted_PIR from projections)
    df["Err_PIR"] = df["Actual_PIR_act"] - df["Predicted_PIR"]
    df["Err_PIR_per_40"] = df["Actual_PIR_per_40_act"] - df["Predicted_PIR_per_40"]

    # In_Range (Floor/Ceiling from projections, actual PIR from actuals)
    df["In_Range"] = ((df["Actual_PIR_act"] >= df["Floor_PIR"]) &
                      (df["Actual_PIR_act"] <= df["Ceiling_PIR"])).astype(int)

    # Save all comparisons
    df.to_csv(os.path.join(RESULTS_DIR, "all_comparisons.csv"), index=False)

    lines = []

    # ══════════════════════════════════════════════════════════════
    # 1. PER-COMPONENT ANALYSIS (per game — στα πραγματικά λεπτά)
    # ══════════════════════════════════════════════════════════════
    lines.append("=" * 70)
    lines.append("PER-COMPONENT ANALYSIS (per game — actual minutes)")
    lines.append("=" * 70)

    comp_results = []
    for comp in COMPONENT_STATS:
        err_col = f"err_{comp}_game"
        abs_err = df[err_col].abs()
        mae = abs_err.mean()
        rmse = np.sqrt((df[err_col] ** 2).mean())
        bias = df[err_col].mean()
        pir_weight = abs(PIR_COEFF[comp])

        # In-range: πόσο συχνά actual μέσα σε [pred ± 0.5*std] (per game)
        std_col = f"std_{comp}"
        if std_col in df.columns:
            min_factor = df["Actual_Minutes"] / 40.0
            lower = df[f"pred_{comp}_game"] - 0.5 * df[std_col] * min_factor
            upper = df[f"pred_{comp}_game"] + 0.5 * df[std_col] * min_factor
            in_range_pct = ((df[f"{comp}_game"] >= lower) & (df[f"{comp}_game"] <= upper)).mean() * 100
        else:
            in_range_pct = np.nan

        comp_results.append({
            "Component": comp,
            "PIR_Coeff": PIR_COEFF[comp],
            "MAE_game": round(mae, 2),
            "RMSE_game": round(rmse, 2),
            "Bias_game": round(bias, 2),
            "In_Range_pct": round(in_range_pct, 1),
            "PIR_Impact_MAE": round(mae * pir_weight, 2),
        })

        lines.append(f"  {comp:<10}  MAE={mae:>6.2f}  RMSE={rmse:>6.2f}  Bias={bias:>+6.2f}  "
                      f"InRange={in_range_pct:>5.1f}%  PIR_impact={mae * pir_weight:>6.2f}")

    df_comp = pd.DataFrame(comp_results)
    df_comp.to_csv(os.path.join(RESULTS_DIR, "per_component.csv"), index=False)

    # ══════════════════════════════════════════════════════════════
    # 2. PIR ANALYSIS
    # ══════════════════════════════════════════════════════════════
    lines.append("")
    lines.append("=" * 70)
    lines.append("PIR PREDICTION ANALYSIS")
    lines.append("=" * 70)

    # Game (actual minutes)
    mae_game = df["Err_PIR"].abs().mean()
    rmse_game = np.sqrt((df["Err_PIR"] ** 2).mean())
    bias_game = df["Err_PIR"].mean()

    # Rate (per 40)
    mae_40 = df["Err_PIR_per_40"].abs().mean()
    rmse_40 = np.sqrt((df["Err_PIR_per_40"] ** 2).mean())
    bias_40 = df["Err_PIR_per_40"].mean()

    # Hit rate
    hit_rate = df["In_Range"].mean() * 100

    lines.append(f"\n  Game Prediction (actual minutes — projected vs actual):")
    lines.append(f"    MAE:  {mae_game:.2f}")
    lines.append(f"    RMSE: {rmse_game:.2f}")
    lines.append(f"    Bias: {bias_game:+.2f}")
    lines.append(f"    Floor/Ceiling Hit Rate: {hit_rate:.1f}%")

    lines.append(f"\n  Rate Prediction (PIR per 40):")
    lines.append(f"    MAE:  {mae_40:.2f}")
    lines.append(f"    RMSE: {rmse_40:.2f}")
    lines.append(f"    Bias: {bias_40:+.2f}")

    # By GP bucket
    lines.append(f"\n  Hit Rate by GP bucket:")
    df["gp_bucket"] = pd.cut(df["GP_before"], bins=[0, 5, 10, 20, 50],
                              labels=["1-5", "6-10", "11-20", "21+"])
    gp_stats = df.groupby("gp_bucket", observed=False).agg(
        hit_rate=("In_Range", "mean"),
        mae=("Err_PIR", lambda x: x.abs().mean()),
        count=("In_Range", "size")
    )
    for bucket, row in gp_stats.iterrows():
        lines.append(f"    GP {bucket:<6}: Hit={row['hit_rate']*100:>5.1f}%  MAE={row['mae']:>5.2f}  (n={int(row['count'])})")

    # By Round
    lines.append(f"\n  Hit Rate by Round (sample):")
    round_stats = df.groupby("Round").agg(
        hit_rate=("In_Range", "mean"),
        mae=("Err_PIR", lambda x: x.abs().mean()),
        count=("In_Range", "size")
    )
    for r, row in round_stats.iterrows():
        if r % 5 == 0 or r == round_stats.index.min() or r == round_stats.index.max():
            lines.append(f"    Round {r:<3}: Hit={row['hit_rate']*100:>5.1f}%  MAE={row['mae']:>5.2f}  (n={int(row['count'])})")

    # ══════════════════════════════════════════════════════════════
    # 3. PER-PLAYER ANALYSIS
    # ══════════════════════════════════════════════════════════════
    lines.append("")
    lines.append("=" * 70)
    lines.append("PER-PLAYER ANALYSIS")
    lines.append("=" * 70)

    player_stats = df.groupby(["Player_ID", "Player"]).agg(
        predictions=("Round", "size"),
        mae_pir=("Err_PIR", lambda x: x.abs().mean()),
        mae_pir_40=("Err_PIR_per_40", lambda x: x.abs().mean()),
        hit_rate=("In_Range", "mean"),
        bias=("Err_PIR", "mean"),
    ).reset_index()
    player_stats = player_stats.sort_values("mae_pir")
    player_stats.to_csv(os.path.join(RESULTS_DIR, "per_player.csv"), index=False)

    lines.append(f"\n  Top 10 most predictable players (lowest MAE):")
    for _, r in player_stats[player_stats["predictions"] >= 10].head(10).iterrows():
        lines.append(f"    {r['Player']:<25} MAE={r['mae_pir']:>5.2f}  Hit={r['hit_rate']*100:>5.1f}%  (n={int(r['predictions'])})")

    lines.append(f"\n  Top 10 least predictable players (highest MAE):")
    for _, r in player_stats[player_stats["predictions"] >= 10].tail(10).iterrows():
        lines.append(f"    {r['Player']:<25} MAE={r['mae_pir']:>5.2f}  Hit={r['hit_rate']*100:>5.1f}%  (n={int(r['predictions'])})")

    # ══════════════════════════════════════════════════════════════
    # 4. PER-PLAYER PER-COMPONENT VOLATILITY
    # ══════════════════════════════════════════════════════════════
    lines.append("")
    lines.append("=" * 70)
    lines.append("PER-PLAYER PER-COMPONENT VOLATILITY")
    lines.append("=" * 70)

    vol_rows = []
    for pid, grp in df.groupby("Player_ID"):
        player_name = grp["Player"].iloc[0]
        n_games = len(grp)
        row = {"Player_ID": pid, "Player": player_name, "Games": n_games}

        for comp in COMPONENT_STATS:
            # Actual volatility (std of actual per-game values)
            row[f"{comp}_actual_std"] = round(grp[f"{comp}_game"].std(), 4) if n_games > 1 else 0.0
            # MAE prediction error per component (per game)
            row[f"{comp}_mae"] = round(grp[f"err_{comp}_game"].abs().mean(), 4)
            # Mean predicted std
            std_col = f"std_{comp}"
            if std_col in grp.columns:
                row[f"{comp}_used_std"] = round(grp[std_col].mean(), 4)

        vol_rows.append(row)

    df_vol = pd.DataFrame(vol_rows)
    df_vol.to_csv(os.path.join(RESULTS_DIR, "per_player_component.csv"), index=False)

    # Summary: μέση volatility ανά component
    lines.append(f"\n  Mean actual volatility (std of actual per-game) across players:")
    for comp in COMPONENT_STATS:
        col = f"{comp}_actual_std"
        mean_vol = df_vol[df_vol["Games"] >= 10][col].mean()
        mean_mae = df_vol[df_vol["Games"] >= 10][f"{comp}_mae"].mean()
        lines.append(f"    {comp:<10}: actual_std={mean_vol:>6.2f}  prediction_MAE={mean_mae:>6.2f}")

    # ── Save summary ───────────────────────────────────────────
    summary_text = "\n".join(lines)
    summary_path = os.path.join(RESULTS_DIR, "summary.txt")
    with open(summary_path, "w") as f:
        f.write(summary_text)

    print(summary_text)
    print(f"\n✅ Αποτελέσματα αποθηκεύτηκαν στο {RESULTS_DIR}/")
    print(f"   - summary.txt")
    print(f"   - per_component.csv")
    print(f"   - per_player.csv")
    print(f"   - per_player_component.csv")
    print(f"   - all_comparisons.csv")


if __name__ == "__main__":
    main()
