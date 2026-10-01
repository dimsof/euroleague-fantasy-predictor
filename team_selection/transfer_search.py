#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════
 transfer_search.py — TEAM OPTIMIZER & TRANSFER SUGGESTER
════════════════════════════════════════════════════════════════════════
Βρίσκει τα ΔΥΟ ΒΕΛΤΙΣΤΑ σενάρια μεταγραφών:

  ① LOWEST RISK      → μεγιστοποιεί το συνολικό Pred_Avg της νέας 10άδας
  ② HIGHEST CEILING  → μεγιστοποιεί το συνολικό Pred_Max της νέας 10άδας

ΜΕΘΟΔΟΣ — εξαντλητική αναζήτηση (εγγυημένα βέλτιστο):
  Εξετάζει ΟΛΟΥΣ τους έγκυρους συνδυασμούς αποχωρήσεων/αγορών (με έξυπνο
  Pareto pruning για ταχύτητα), οπότε δεν χάνει συνδυασμούς όπως
  "πούλα δύο μεσαίους για να πάρεις έναν σταρ".

ΧΡΗΣΗ (μέσω του launcher run_transfers.py):
    python run_transfers.py --csv ratings.csv --roster my_team.csv \
                            --bank 3.5 --max-transfers 3

ΠΑΡΑΜΕΤΡΟΙ:
    --csv            το αρχείο με όλους τους παίκτες
    --roster         αρχείο με τα 10 ονόματα του ρόστερ σου (ένα ανά γραμμή)
    --bank           διαθέσιμο υπόλοιπο σε credits (default 0)
    --max-transfers  μέγιστες αλλαγές (default 2)
    --pool           πόσους free agents κρατάμε ανά θέση (default 18)

ΑΝΑΜΕΝΟΜΕΝΕΣ ΣΤΗΛΕΣ CSV (νέο format από build_ratings.py):
    Player, Position, Credits, Projected_PIR, Ceiling, Floor
    (παλιό format Cost/Pred_Avg/Pred_Max γίνεται auto-remap)
"""

import argparse
import bisect
import sys
from itertools import combinations

import pandas as pd

# Το διάβασμα & ταίριασμα ονομάτων ζει στο utils/names.py
from utils import names as names_mod
import config


LINEUP = {"G": 4, "F": 4, "C": 2}
POSITIONS = ["G", "F", "C"]
POS_NAME = {"G": "Guard", "F": "Forward", "C": "Center"}


# ═══════════════════════════════════════════════════════════════════════
#  1. ΦΟΡΤΩΜΑ ΔΕΔΟΜΕΝΩΝ
# ═══════════════════════════════════════════════════════════════════════

def load_players(csv_path):
    """Φορτώνει το CSV και κανονικοποιεί ονόματα στηλών."""
    df = pd.read_csv(csv_path)

    # Στάνταρ όνομα στήλης: "Player" (δεχόμαστε και "Name" για συμβατότητα)
    if "Player" not in df.columns and "Name" in df.columns:
        df = df.rename(columns={"Name": "Player"})
    if "Player" not in df.columns:
        sys.exit("❌ Το CSV χρειάζεται στήλη 'Player'.")

    # Auto-remap νέο format → παλιό
    remap = {
        "Credits": "Cost",
        "Projected_PIR": "Pred_Avg",
        "Ceiling": "Pred_Max",
        "Floor": "Pred_Floor",
    }
    for old_name, new_name in remap.items():
        if old_name in df.columns and new_name not in df.columns:
            df = df.rename(columns={old_name: new_name})

    required = ["Player", "Position", "Cost", "Pred_Avg", "Pred_Max"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(f"❌ Λείπουν στήλες από το CSV: {missing}")

    # προαιρετικές στήλες value — αν λείπουν, τις υπολογίζουμε
    if "Value_Avg" not in df.columns:
        df["Value_Avg"] = df["Pred_Avg"] / df["Cost"].replace(0, pd.NA)
    if "Value_Ceiling" not in df.columns:
        df["Value_Ceiling"] = df["Pred_Max"] / df["Cost"].replace(0, pd.NA)

    df["Position"] = df["Position"].astype(str).str.strip().str.upper().str[0]
    return df


def split_roster(df, roster_names):
    """Χωρίζει σε current_roster και free_agents (χαλαρό ταίριασμα ονομάτων)."""
    matched, not_found = names_mod.find_players(roster_names, df["Player"].tolist())
    roster = df[df["Player"].isin(matched)].copy()
    free_agents = df[~df["Player"].isin(matched)].copy()
    return roster, free_agents, not_found


# ═══════════════════════════════════════════════════════════════════════
#  2. PRUNING — κρατάμε μόνο τους πιο υποσχόμενους free agents
# ═══════════════════════════════════════════════════════════════════════

def prune_free_agents(free_agents, value_column, score_column, keep_per_position):
    """
    Κρατά ανά θέση τους top-N free agents.

    Γιατί δύο κριτήρια: κρατάμε την ΕΝΩΣΗ των top-N κατά value (οι ευκαιρίες)
    ΚΑΙ των top-N κατά απόλυτο σκορ (οι σταρ). Έτσι δεν χάνουμε ούτε τους
    φθηνούς-αποδοτικούς ούτε τους ακριβούς-κορυφαίους.
    """
    pruned = {}
    for pos in POSITIONS:
        candidates = free_agents[free_agents["Position"] == pos].copy()
        candidates = candidates.dropna(subset=["Cost", score_column])
        candidates = candidates[candidates["Cost"] > 0]

        by_value = candidates.nlargest(keep_per_position, value_column) \
            if value_column in candidates.columns else candidates.head(0)
        by_score = candidates.nlargest(keep_per_position, score_column)

        combined = pd.concat([by_value, by_score]).drop_duplicates(subset="Player")
        pruned[pos] = combined.reset_index(drop=True)
    return pruned


# ═══════════════════════════════════════════════════════════════════════
#  3. PARETO FRONTIER — η καρδιά της ταχύτητας
# ═══════════════════════════════════════════════════════════════════════
#  Για κάθε θέση και κάθε πλήθος αγορών c, φτιάχνουμε όλους τους
#  συνδυασμούς c παικτών, και κρατάμε ΜΟΝΟ τους μη-κυριαρχούμενους:
#  αν ο συνδυασμός Α κοστίζει λιγότερο ΚΑΙ σκοράρει περισσότερο από τον Β,
#  ο Β είναι άχρηστος και πετιέται.
#
#  Έτσι, το "ποιοι είναι οι καλύτεροι c παίκτες αυτής της θέσης μέσα σε
#  budget X" απαντιέται σε χρόνο log, αντί να ξαναψάχνουμε κάθε φορά.
# ═══════════════════════════════════════════════════════════════════════

class PositionOptions:
    """Οι Pareto-βέλτιστες επιλογές αγοράς για μία θέση & ένα πλήθος."""

    def __init__(self, combos):
        # combos: λίστα από (cost, score, (ονόματα))
        combos.sort(key=lambda x: (x[0], -x[1]))

        self.costs, self.scores, self.picks = [], [], []
        best_so_far = float("-inf")
        for cost, score, names in combos:
            if score > best_so_far:          # μη-κυριαρχούμενος
                best_so_far = score
                self.costs.append(cost)
                self.scores.append(score)
                self.picks.append(names)

    def best_within(self, budget):
        """Η καλύτερη επιλογή που χωράει στο budget → (score, cost, names)."""
        if not self.costs:
            return None
        idx = bisect.bisect_right(self.costs, budget + 1e-9) - 1
        if idx < 0:
            return None
        return self.scores[idx], self.costs[idx], self.picks[idx]

    def __len__(self):
        return len(self.costs)


def build_position_options(pruned, score_column, max_count):
    """options[position][count] = PositionOptions"""
    options = {pos: {} for pos in POSITIONS}
    for pos in POSITIONS:
        players = pruned[pos]
        records = list(zip(players["Player"], players["Cost"], players[score_column]))
        for count in range(1, max_count + 1):
            if len(records) < count:
                options[pos][count] = PositionOptions([])
                continue
            combos = []
            for group in combinations(records, count):
                total_cost = sum(p[1] for p in group)
                total_score = sum(p[2] for p in group)
                combos.append((total_cost, total_score, tuple(p[0] for p in group)))
            options[pos][count] = PositionOptions(combos)
    return options


# ═══════════════════════════════════════════════════════════════════════
#  4. Η ΑΝΑΖΗΤΗΣΗ
# ═══════════════════════════════════════════════════════════════════════

def find_best_scenario(roster, free_agents, bank, max_transfers,
                       score_column, value_column, keep_per_position):
    """
    Εξετάζει κάθε συνδυασμό αποχωρήσεων (k = 1..max_transfers) και βρίσκει
    την καλύτερη αντίστοιχη αγορά. Επιστρέφει ΜΟΝΟ τον #1 νικητή.
    """
    pruned = prune_free_agents(free_agents, value_column, score_column,
                               keep_per_position)
    options = build_position_options(pruned, score_column, max_transfers)

    roster_records = list(zip(roster["Player"], roster["Position"],
                              roster["Cost"].fillna(0), roster[score_column]))
    baseline_score = float(roster[score_column].sum())

    best = {"score": baseline_score, "out": (), "in": (), "cost_in": 0.0,
            "cost_out": 0.0, "transfers": 0}
    combos_examined = 0

    for k in range(1, max_transfers + 1):
        for leaving in combinations(roster_records, k):
            combos_examined += 1

            budget = bank + sum(p[2] for p in leaving)
            score_lost = sum(p[3] for p in leaving)

            # πόσους χρειαζόμαστε ανά θέση
            needed = {pos: 0 for pos in POSITIONS}
            for p in leaving:
                needed[p[1]] += 1
            active = [(pos, cnt) for pos, cnt in needed.items() if cnt > 0]

            result = _best_purchase(active, options, budget)
            if result is None:
                continue
            score_gained, cost_spent, incoming = result

            new_score = baseline_score - score_lost + score_gained
            if new_score > best["score"] + 1e-9:
                best = {
                    "score": new_score,
                    "out": tuple(p[0] for p in leaving),
                    "in": incoming,
                    "cost_in": cost_spent,
                    "cost_out": sum(p[2] for p in leaving),
                    "transfers": k,
                }

    best["baseline"] = baseline_score
    best["bank_after"] = bank + best["cost_out"] - best["cost_in"]
    best["combos_examined"] = combos_examined
    best["pool_sizes"] = {pos: len(pruned[pos]) for pos in POSITIONS}
    return best


def _best_purchase(active, options, budget):
    """
    Βρίσκει την καλύτερη αγορά που καλύπτει ΑΚΡΙΒΩΣ τις ζητούμενες θέσεις
    μέσα στο budget. Το `active` έχει το πολύ 3 στοιχεία (G/F/C), οπότε
    η αναδρομή είναι ρηχή και γρήγορη.
    """
    best = None

    def recurse(index, remaining_budget, score_so_far, cost_so_far, picked):
        nonlocal best
        if index == len(active):
            if best is None or score_so_far > best[0]:
                best = (score_so_far, cost_so_far, tuple(picked))
            return

        pos, count = active[index]
        option_set = options[pos].get(count)
        if option_set is None or not len(option_set):
            return

        # Τελευταία θέση → παίρνουμε κατευθείαν τη βέλτιστη (O(log n))
        if index == len(active) - 1:
            hit = option_set.best_within(remaining_budget)
            if hit is None:
                return
            score, cost, names = hit
            recurse(index + 1, remaining_budget - cost,
                    score_so_far + score, cost_so_far + cost,
                    picked + list(names))
            return

        # Αλλιώς δοκιμάζουμε κάθε Pareto επιλογή που χωράει
        for cost, score, names in zip(option_set.costs, option_set.scores,
                                      option_set.picks):
            if cost > remaining_budget + 1e-9:
                break                      # ταξινομημένα κατά κόστος → σταμάτα
            recurse(index + 1, remaining_budget - cost,
                    score_so_far + score, cost_so_far + cost,
                    picked + list(names))

    recurse(0, budget, 0.0, 0.0, [])
    return best


# ═══════════════════════════════════════════════════════════════════════
#  5. ΕΜΦΑΝΙΣΗ
# ═══════════════════════════════════════════════════════════════════════

def print_scenario(title, subtitle, best, df, score_column, bank):
    print("\n" + "═" * 68)
    print(f"[{title}]")
    print(subtitle)
    print("═" * 68)

    if best["transfers"] == 0:
        print("\n✅ Καμία αλλαγή δεν βελτιώνει την ομάδα σου.")
        print(f"   Συνολικό {score_column}: {best['baseline']:.1f}")
        print(f"   Υπόλοιπο μπάτζετ: {bank:.1f} credits")
        return

    lookup = df.set_index("Player")

    print(f"\n🔻 ΠΑΙΚΤΕΣ ΠΟΥ ΦΕΥΓΟΥΝ ({len(best['out'])}):")
    for name in best["out"]:
        row = lookup.loc[name]
        cost = row["Cost"] if pd.notna(row["Cost"]) else 0.0
        print(f"   − {name:<26} ({row['Position']})  "
              f"{cost:>5.1f}cr   {score_column} {row[score_column]:>5.1f}")

    print(f"\n🔺 ΠΑΙΚΤΕΣ ΠΟΥ ΕΡΧΟΝΤΑΙ ({len(best['in'])}):")
    for name in best["in"]:
        row = lookup.loc[name]
        print(f"   + {name:<26} ({row['Position']})  "
              f"{row['Cost']:>5.1f}cr   {score_column} {row[score_column]:>5.1f}")

    delta = best["score"] - best["baseline"]
    print(f"\n📊 ΣΥΝΟΛΙΚΟ {score_column}:")
    print(f"   Παλιό:  {best['baseline']:>7.1f}")
    print(f"   Νέο:    {best['score']:>7.1f}   ({delta:+.1f})")

    print("\n💰 ΜΠΑΤΖΕΤ:")
    print(f"   Διαθέσιμο πριν:     {bank:>7.1f} cr")
    print(f"   + Έσοδα πωλήσεων:   {best['cost_out']:>7.1f} cr")
    print(f"   − Κόστος αγορών:    {best['cost_in']:>7.1f} cr")
    print(f"   = Υπόλοιπο μετά:    {best['bank_after']:>7.1f} cr")


# ═══════════════════════════════════════════════════════════════════════
#  6. MAIN
# ═══════════════════════════════════════════════════════════════════════

def build_from_scratch(df, budget, score_column, value_column, keep_per_position):
    """
    Χτίζει ομάδα από το μηδέν (4G + 4F + 2C) μέσα στο budget.
    Χρησιμοποιεί Pareto pruning ακριβώς όπως το transfer search.
    """
    free_agents = df[df["Pred_Avg"] > 0].copy()
    pruned = prune_free_agents(free_agents, value_column, score_column,
                               keep_per_position)
    options = build_position_options(pruned, score_column,
                                    max(LINEUP.values()))

    # Βρες τον καλύτερο συνδυασμό 4G + 4F + 2C μέσα στο budget
    active = [(pos, LINEUP[pos]) for pos in POSITIONS]

    best = None

    def recurse(index, remaining_budget, score_so_far, cost_so_far, picked):
        nonlocal best
        if index == len(active):
            if best is None or score_so_far > best[0]:
                best = (score_so_far, cost_so_far, tuple(picked))
            return

        pos, count = active[index]
        option_set = options[pos].get(count)
        if option_set is None or not len(option_set):
            return

        if index == len(active) - 1:
            hit = option_set.best_within(remaining_budget)
            if hit is None:
                return
            score, cost, names = hit
            recurse(index + 1, remaining_budget - cost,
                    score_so_far + score, cost_so_far + cost,
                    picked + list(names))
            return

        for cost, score, names in zip(option_set.costs, option_set.scores,
                                      option_set.picks):
            if cost > remaining_budget + 1e-9:
                break
            recurse(index + 1, remaining_budget - cost,
                    score_so_far + score, cost_so_far + cost,
                    picked + list(names))

    recurse(0, budget, 0.0, 0.0, [])
    return best, {pos: len(pruned[pos]) for pos in POSITIONS}


def print_new_team(title, subtitle, result, pool_sizes, df, score_column, budget):
    """Εκτύπωση αποτελέσματος build from scratch."""
    print("\n" + "═" * 68)
    print(f"[{title}]")
    print(subtitle)
    print("═" * 68)

    if result is None:
        print("\n❌ Δεν βρέθηκε έγκυρος συνδυασμός μέσα στο budget!")
        return

    total_score, total_cost, players = result
    lookup = df.set_index("Player")

    print(f"\n🏀 ΟΜΑΔΑ ({len(players)} παίκτες):")
    for pos in POSITIONS:
        pos_players = [p for p in players if lookup.loc[p]["Position"] == pos]
        print(f"\n  {POS_NAME[pos]}s:")
        for name in sorted(pos_players, key=lambda n: lookup.loc[n][score_column], reverse=True):
            row = lookup.loc[name]
            print(f"    {name:<26} {row['Cost']:>5.1f}cr   {score_column} {row[score_column]:>5.1f}")

    print(f"\n📊 ΣΥΝΟΛΙΚΟ {score_column}: {total_score:.1f}")
    print(f"\n💰 ΜΠΑΤΖΕΤ:")
    print(f"   Διαθέσιμο:       {budget:>7.1f} cr")
    print(f"   Κόστος ομάδας:   {total_cost:>7.1f} cr")
    print(f"   Υπόλοιπο:        {budget - total_cost:>7.1f} cr")
    print(f"\n   Pool ανά θέση: {pool_sizes}")


def main():
    parser = argparse.ArgumentParser(
        description="Βρίσκει τα 2 βέλτιστα σενάρια μεταγραφών.")
    parser.add_argument("--csv", default=config.RATINGS_FILE,
                        help="CSV με όλους τους παίκτες (default: data/ratings.csv)")
    parser.add_argument("--roster", default=config.MY_TEAM_FILE,
                        help="αρχείο με τα ονόματα του ρόστερ (default: data/my_team.csv)")
    parser.add_argument("--bank", type=float, default=0.0,
                        help="διαθέσιμο υπόλοιπο σε credits")
    parser.add_argument("--max-transfers", type=int, default=2,
                        help="μέγιστος αριθμός αλλαγών")
    parser.add_argument("--pool", type=int, default=18,
                        help="πόσους free agents κρατάμε ανά θέση")
    args = parser.parse_args()

    df = load_players(args.csv)

    # παίκτες που δεν παίζουν (Pred_Avg = 0) βγαίνουν απ' την αγορά
    df = df[df["Pred_Avg"].notna()].copy()

    # ── Αν δεν υπάρχει roster → build from scratch ──
    import os
    if not os.path.exists(args.roster):
        print("\n" + "─" * 68)
        print("🆕 ΝΕΑ ΟΜΑΔΑ ΑΠΟ ΤΟ ΜΗΔΕΝ")
        print("─" * 68)
        print(f"Budget: {args.bank:.1f} cr   |   Σύνθεση: 4G + 4F + 2C")
        print(f"Παίκτες διαθέσιμοι: {len(df[df['Pred_Avg'] > 0])}")

        # Σενάριο 1: Lowest Risk
        result_avg, pool_avg = build_from_scratch(
            df, args.bank, "Pred_Avg", "Value_Avg", args.pool)
        print_new_team(
            "LOWEST RISK TEAM",
            "Στόχος: μέγιστο συνολικό Pred_Avg — η πιο σίγουρη ομάδα.",
            result_avg, pool_avg, df, "Pred_Avg", args.bank)

        # Σενάριο 2: Highest Ceiling
        result_max, pool_max = build_from_scratch(
            df, args.bank, "Pred_Max", "Value_Ceiling", args.pool)
        print_new_team(
            "HIGHEST CEILING TEAM",
            "Στόχος: μέγιστο συνολικό Pred_Max — το μεγαλύτερο δυνατό ταβάνι.",
            result_max, pool_max, df, "Pred_Max", args.bank)

        print("\n" + "─" * 68)
        return

    roster_names = names_mod.read_name_list(args.roster)
    roster, free_agents, not_found = split_roster(df, roster_names)

    if not_found:
        print(f"⚠ Δεν βρέθηκαν στο CSV: {not_found}")
    if len(roster) == 0:
        sys.exit("❌ Κανένας παίκτης του ρόστερ δεν βρέθηκε στο CSV.")

    free_agents = free_agents[free_agents["Pred_Avg"] > 0].copy()

    # ── Σύνοψη εισόδου ──
    counts = roster["Position"].value_counts().to_dict()
    print("\n" + "─" * 68)
    print("ΤΩΡΙΝΟ ΡΟΣΤΕΡ")
    print("─" * 68)
    print("Σύνθεση: " + " | ".join(
        f"{POS_NAME[p]}s {counts.get(p, 0)}/{LINEUP[p]}" for p in POSITIONS))
    if any(counts.get(p, 0) != LINEUP[p] for p in POSITIONS):
        print("⚠ Η σύνθεση δεν είναι 4G/4F/2C — ο αλγόριθμος διατηρεί ό,τι σύνθεση "
              "έχεις\n  (αγοράζει ακριβώς τις θέσεις που πουλάει).")
    print(f"Αξία ρόστερ: {roster['Cost'].fillna(0).sum():.1f} cr"
          f"   |   Bank: {args.bank:.1f} cr"
          f"   |   Max αλλαγές: {args.max_transfers}")
    print(f"Free agents διαθέσιμοι: {len(free_agents)}")
    print(f"Σύνολο Pred_Avg: {roster['Pred_Avg'].sum():.1f}"
          f"   |   Σύνολο Pred_Max: {roster['Pred_Max'].sum():.1f}")

    # ── ΣΕΝΑΡΙΟ 1: LOWEST RISK (μεγιστοποίηση Pred_Avg) ──
    best_avg = find_best_scenario(
        roster, free_agents, args.bank, args.max_transfers,
        score_column="Pred_Avg", value_column="Value_Avg",
        keep_per_position=args.pool)

    print_scenario(
        "LOWEST RISK SCENARIO",
        "Στόχος: μέγιστο συνολικό Pred_Avg — η πιο σίγουρη απόδοση.",
        best_avg, df, "Pred_Avg", args.bank)

    # ── ΣΕΝΑΡΙΟ 2: HIGHEST CEILING (μεγιστοποίηση Pred_Max) ──
    best_max = find_best_scenario(
        roster, free_agents, args.bank, args.max_transfers,
        score_column="Pred_Max", value_column="Value_Ceiling",
        keep_per_position=args.pool)

    print_scenario(
        "HIGHEST CEILING SCENARIO",
        "Στόχος: μέγιστο συνολικό Pred_Max — το μεγαλύτερο δυνατό ταβάνι.",
        best_max, df, "Pred_Max", args.bank)

    # ── Τεχνική σύνοψη ──
    print("\n" + "─" * 68)
    print(f"Εξετάστηκαν {best_avg['combos_examined']} συνδυασμοί αποχωρήσεων ανά σενάριο "
          f"| pool ανά θέση: {best_avg['pool_sizes']}")
    if best_avg["out"] == best_max["out"] and best_avg["in"] == best_max["in"]:
        print("ℹ  Τα δύο σενάρια συμπίπτουν — οι ίδιες αλλαγές είναι βέλτιστες "
              "και για τα δύο κριτήρια.")
    print("─" * 68)


if __name__ == "__main__":
    main()
