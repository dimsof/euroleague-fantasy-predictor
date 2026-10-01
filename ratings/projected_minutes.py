#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════
 projected_minutes.py  —  Δυναμικός υπολογισμός projected minutes
════════════════════════════════════════════════════════════════════
Παράγει: data/projected_minutes.csv

Αλγόριθμος (δυναμικός, βάσει τελευταίου αγώνα):
  ΒΗΜΑ 1: Πραγματικά λεπτά ανά θέση/ομάδα από τον ΤΕΛΕΥΤΑΙΟ αγώνα.
  ΒΗΜΑ 2: Ανά θέση, 3 περιπτώσεις:
    Α) Ίδιοι παίκτες  → ίδια κατανομή λεπτών
    Β) Λείπει κάποιος  → τα λεπτά του μοιράζονται ποσοστιαία
       (περισσότερα λεπτά = μεγαλύτερο μερίδιο)
    Γ) Επιστρέφει κάποιος → 90% του MPG, αφαιρούνται
       ποσοστιαία από τους υπόλοιπους
  ΒΗΜΑ 3: Cap ανά παίκτη = 2× MPG (πλεονάσματα εντός θέσης)
  ΒΗΜΑ 4: Κανονικοποίηση σε 200 λεπτά ανά ομάδα:
    Α) < 200 → αύξηση ποσοστιαία
    Β) > 200 → μείωση στη θέση με τη μεγαλύτερη αύξηση
                vs τελευταίο αγώνα
"""

import os
import sys
import glob
import pandas as pd
import numpy as np

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)

# ── Config ──────────────────────────────────────────────────────
TEAM_TOTAL = 200       # 5 παίκτες × 40' = 200 λεπτά ανά ομάδα
CAP_FACTOR = 2.0       # cap = 2× MPG ανά παίκτη
RETURN_PCT = 0.90      # επιστρέφων παίκτης → 90% του MPG
POSITIONS  = ["Guard", "Forward", "Center"]


# ══════════════════════════════════════════════════════════════════
# ΒΟΗΘΗΤΙΚΑ
# ══════════════════════════════════════════════════════════════════

def parse_minutes(m):
    """'MM:SS' → float λεπτά."""
    if pd.isna(m):
        return 0.0
    parts = str(m).split(":")
    if len(parts) == 2:
        try:
            return int(parts[0]) + int(parts[1]) / 60
        except ValueError:
            return 0.0
    return 0.0


def baseline_mpg(credits):
    """Εκτίμηση MPG βάσει credits: (cr − 4) × 2.  cr ≤ 4 → 0."""
    if pd.isna(credits) or credits <= 4:
        return 0.0
    return (credits - 4) * 2


def load_anchor(year):
    """Φορτώνει anchor_{year}.csv αν υπάρχει."""
    path = os.path.join(DATA_DIR, f"anchor_{year}.csv")
    if os.path.exists(path):
        return pd.read_csv(path), True
    return None, False


def find_latest_boxscores():
    """Βρίσκει το πιο πρόσφατο boxscores_YYYY.csv στο data/."""
    files = glob.glob(os.path.join(DATA_DIR, "boxscores_*.csv"))
    if not files:
        return None

    def year_of(f):
        try:
            return int(
                os.path.basename(f).replace("boxscores_", "").replace(".csv", "")
            )
        except ValueError:
            return 0

    return max(files, key=year_of)


def load_injuries(path=None):
    """Injuries → dict {player_id: minutes_factor}."""
    if path is None:
        path = os.path.join(DATA_DIR, "injuries.csv")
    if not os.path.exists(path):
        return {}
    try:
        inj = pd.read_csv(path)
        if "minutes_factor" in inj.columns and "player_id" in inj.columns:
            inj["player_id"] = inj["player_id"].str.strip()
            return dict(zip(inj["player_id"], inj["minutes_factor"]))
    except Exception:
        pass
    return {}


class MPGLookup:
    """Αναζήτηση MPG: anchor 2026 → 2025 → 2024 → baseline credits."""

    def __init__(self, credits_map):
        self._anchors = []          # [(DataFrame, year), ...]
        self._credits = credits_map
        self._cache   = {}

    def add(self, year):
        df, ok = load_anchor(year)
        if ok:
            self._anchors.append((df, year))

    @property
    def years(self):
        return [y for _, y in self._anchors]

    def get(self, pid):
        if pid in self._cache:
            return self._cache[pid]
        for df, _ in self._anchors:
            row = df[df["Player_ID"] == pid]
            if len(row) and row.iloc[0]["GP"] > 0:
                val = row.iloc[0]["MPG"]
                self._cache[pid] = val
                return val
        val = baseline_mpg(self._credits.get(pid, 0))
        self._cache[pid] = val
        return val


# ══════════════════════════════════════════════════════════════════
# ΚΥΡΙΑ ΛΟΓΙΚΗ
# ══════════════════════════════════════════════════════════════════

def _project_position(avail_pos, last_pos, mpg_lookup, inj_factors):
    """
    Υπολογίζει projected minutes για ΜΙΑ θέση μιας ομάδας.
    Βήματα 2 + 3 του αλγορίθμου.

    avail_pos : set of pid — διαθέσιμοι παίκτες (όχι OUT)
    last_pos  : dict {pid: minutes} — πραγματικά λεπτά τελευταίου αγώνα
    mpg_lookup: MPGLookup instance
    inj_factors: dict {pid: factor} — injury factors

    Επιστρέφει dict {pid: projected_minutes}.
    """
    proj = {}

    # ── ΒΗΜΑ 2Α: Παίκτες που ήταν ΚΑΙ στον τελευταίο αγώνα ──────
    for pid in last_pos:
        if pid in avail_pos:
            proj[pid] = last_pos[pid]

    # ── ΒΗΜΑ 2Β: Λείπουν (τραυματίες / μη διαθέσιμοι) ───────────
    missing_min = sum(last_pos[p] for p in last_pos if p not in avail_pos)
    if missing_min > 0 and proj:
        total_rem = sum(proj.values())
        if total_rem > 0:
            for pid in proj:
                proj[pid] += missing_min * proj[pid] / total_rem
        else:
            # Όλοι οι υπόλοιποι είχαν 0' → ισόποσα
            share = missing_min / len(proj)
            for pid in proj:
                proj[pid] += share

    # ── ΒΗΜΑ 2Γ: Επιστρέφουν παίκτες (δεν ήταν στο τελευταίο ματς) ─
    for pid in avail_pos:
        if pid not in last_pos:
            ret = mpg_lookup.get(pid) * RETURN_PCT
            if ret <= 0:
                proj[pid] = 0.0
                continue
            proj[pid] = ret
            # Αφαίρεση ποσοστιαία από τους υπόλοιπους
            others = {p: m for p, m in proj.items() if p != pid and m > 0}
            ot = sum(others.values())
            if ot > 0:
                for p in others:
                    proj[p] -= ret * others[p] / ot
                    proj[p] = max(0, proj[p])

    # ── Partial injuries (0 < factor < 1) ─────────────────────────
    for pid in list(proj):
        f = inj_factors.get(pid, 1.0)
        if 0 < f < 1:
            freed = proj[pid] * (1 - f)
            proj[pid] *= f
            # Μοιρασμός στους υγιείς της ίδιας θέσης
            healthy = {p: proj[p] for p in proj
                       if p != pid and inj_factors.get(p, 1.0) >= 1.0
                       and proj[p] > 0}
            ht = sum(healthy.values())
            if ht > 0:
                for p in healthy:
                    proj[p] += freed * healthy[p] / ht

    # ── ΒΗΜΑ 3: Cap στο 2× MPG ───────────────────────────────────
    for _ in range(5):  # iterate γιατί μετά το redistribute μπορεί να ξεπεράσει κάποιος
        excess = 0.0
        for pid in proj:
            cap = max(mpg_lookup.get(pid) * CAP_FACTOR, 1.0)
            if proj[pid] > cap:
                excess += proj[pid] - cap
                proj[pid] = cap
        if excess < 0.1:
            break
        # Μοιρασμός πλεονάσματος σε μη-capped
        uncapped = {p: proj[p] for p in proj
                    if proj[p] < max(mpg_lookup.get(p) * CAP_FACTOR, 1.0)}
        ut = sum(uncapped.values())
        if ut > 0:
            for p in uncapped:
                proj[p] += excess * uncapped[p] / ut
        else:
            break  # όλοι στο cap

    # Ασφάλεια: min 0
    for pid in proj:
        proj[pid] = max(0.0, proj[pid])

    return proj


def _normalize_team(team_proj, pos_map, last_pos_totals, has_last):
    """
    ΒΗΜΑ 4: κανονικοποίηση στα 200 λεπτά ανά ομάδα.
    Τροποποιεί team_proj in-place.
    """
    team_total = sum(team_proj.values())
    if team_total <= 0:
        return

    diff = TEAM_TOTAL - team_total
    if abs(diff) <= 0.1:
        return

    if diff > 0:
        # ── Α) Κάτω από 200 → αύξηση ποσοστιαία ──
        factor = TEAM_TOTAL / team_total
        for pid in team_proj:
            team_proj[pid] *= factor
    else:
        # ── Β) Πάνω από 200 → μείωση στις φουσκωμένες θέσεις ──
        excess = team_total - TEAM_TOTAL

        if has_last:
            # Ποια θέση φούσκωσε περισσότερο vs τελευταίο αγώνα;
            inflation = {}
            for pos in POSITIONS:
                curr = sum(team_proj.get(p, 0) for p in team_proj
                           if pos_map.get(p) == pos)
                inflation[pos] = max(0, curr - last_pos_totals.get(pos, 0))

            total_infl = sum(inflation.values())

            if total_infl > 0:
                for pos in POSITIONS:
                    if inflation[pos] <= 0:
                        continue
                    pos_red = excess * inflation[pos] / total_infl
                    pids = [p for p in team_proj if pos_map.get(p) == pos]
                    pt = sum(team_proj[p] for p in pids)
                    if pt > 0:
                        for p in pids:
                            team_proj[p] -= pos_red * team_proj[p] / pt
                            team_proj[p] = max(0, team_proj[p])
            else:
                # Fallback ποσοστιαία
                factor = TEAM_TOTAL / team_total
                for pid in team_proj:
                    team_proj[pid] *= factor
        else:
            factor = TEAM_TOTAL / team_total
            for pid in team_proj:
                team_proj[pid] *= factor

    # Final safety: ακριβώς 200
    ft = sum(team_proj.values())
    if ft > 0 and abs(ft - TEAM_TOTAL) > 0.1:
        adj = TEAM_TOTAL / ft
        for pid in team_proj:
            team_proj[pid] *= adj


def compute_projected_minutes(injuries_file=None):
    """Κύρια συνάρτηση: δυναμικός υπολογισμός projected minutes."""

    # ── 1. Players stats (βάση παικτών) ───────────────────────────
    ps = pd.read_excel(os.path.join(DATA_DIR, "players_stats.xlsx"))
    ps = ps[ps["Name"].notna() & ps["Surname"].notna()].copy()
    ps = ps[ps["Position"].isin(POSITIONS)].copy()
    ps = ps[pd.to_numeric(ps["ID"], errors="coerce").notna()].copy()
    ps["Player_ID"] = "P" + ps["ID"].astype(int).astype(str).str.zfill(6)
    ps["Player"]    = ps["Name"] + " " + ps["Surname"]

    pos_map     = dict(zip(ps["Player_ID"], ps["Position"]))
    credits_map = dict(zip(ps["Player_ID"], ps["Quotation"]))
    print(f"→ {len(ps)} παίκτες από players_stats.xlsx")

    # ── 2. Boxscores (τελευταίος αγώνας) ──────────────────────────
    box_path = find_latest_boxscores()
    if box_path and os.path.exists(box_path):
        box = pd.read_csv(box_path)
        box["Player_ID"] = box["Player_ID"].str.strip()
        box["Min"] = box["Minutes"].apply(parse_minutes)
        has_box = True
        print(f"→ Boxscores: {os.path.basename(box_path)}, "
              f"rounds {sorted(box['Round'].unique())}")
    else:
        box = pd.DataFrame()
        has_box = False
        print("→ Χωρίς boxscores — MPG fallback")

    # ── 3. Anchors (MPG lookup) ───────────────────────────────────
    mpg_lookup = MPGLookup(credits_map)
    for y in [2026, 2025, 2024]:
        mpg_lookup.add(y)
    print(f"→ Anchors: {', '.join(map(str, mpg_lookup.years)) if mpg_lookup.years else 'ΚΑΝΕΝΑ'}")

    # ── 4. Injuries ───────────────────────────────────────────────
    inj_factors = load_injuries(injuries_file)
    if inj_factors:
        out_n  = sum(1 for v in inj_factors.values() if v == 0.0)
        part_n = sum(1 for v in inj_factors.values() if 0 < v < 1)
        print(f"→ Injuries: {len(inj_factors)} "
              f"({out_n} OUT, {part_n} partial)")
    else:
        print("→ Χωρίς injury report")

    # ── 5. Υπολογισμός ανά ομάδα ──────────────────────────────────
    all_proj = {}       # pid → projected minutes

    for team in sorted(ps["Team"].unique()):
        team_pids = set(ps[ps["Team"] == team]["Player_ID"])
        available = {p for p in team_pids if inj_factors.get(p, 1.0) > 0}

        # Τελευταίος αγώνας αυτής της ομάδας
        if has_box and team in box["Team"].values:
            tb = box[box["Team"] == team]
            last_rd = tb["Round"].max()
            lg = tb[tb["Round"] == last_rd]
            last_min = {}
            for _, r in lg.iterrows():
                pid = r["Player_ID"]
                if pid in pos_map:
                    last_min[pid] = r["Min"]
            has_last = True
        else:
            last_min = {}
            has_last = False

        team_proj = {}
        last_pos_totals = {}

        for pos in POSITIONS:
            avail_pos = {p for p in available if pos_map.get(p) == pos}
            last_pos  = {p: m for p, m in last_min.items()
                         if pos_map.get(p) == pos}
            last_pos_totals[pos] = sum(last_pos.values())

            if not avail_pos:
                # Κενή θέση — Step 4 θα κανονικοποιήσει
                continue

            if not has_last or not last_pos:
                # Χωρίς δεδομένα τελευταίου αγώνα → MPG fallback
                for pid in avail_pos:
                    f = inj_factors.get(pid, 1.0)
                    team_proj[pid] = mpg_lookup.get(pid) * f
                continue

            # Δυναμικός υπολογισμός (Βήματα 2 + 3)
            proj = _project_position(avail_pos, last_pos,
                                     mpg_lookup, inj_factors)
            team_proj.update(proj)

        # ΒΗΜΑ 4: Κανονικοποίηση σε 200
        _normalize_team(team_proj, pos_map, last_pos_totals, has_last)

        all_proj.update(team_proj)

    # ── 6. Output ─────────────────────────────────────────────────
    rows = []
    for _, r in ps.iterrows():
        pid = r["Player_ID"]
        rows.append({
            "Player_ID": pid,
            "Player":    r["Player"],
            "Team":      r["Team"],
            "Position":  r["Position"],
            "Projected_Minutes": round(max(0, all_proj.get(pid, 0.0)), 1),
        })

    output = pd.DataFrame(rows)
    output = output.sort_values(
        ["Team", "Position", "Projected_Minutes"],
        ascending=[True, True, False],
    )

    out_path = os.path.join(DATA_DIR, "projected_minutes.csv")
    output.to_csv(out_path, index=False)

    # Summary
    print(f"\n✅ {out_path}: {len(output)} παίκτες")
    print(f"\n{'Team':<6} {'G':>6} {'F':>6} {'C':>6} {'Total':>7}")
    print("─" * 35)
    for team in sorted(ps["Team"].unique()):
        t = output[output["Team"] == team]
        g  = t[t["Position"] == "Guard"]["Projected_Minutes"].sum()
        fw = t[t["Position"] == "Forward"]["Projected_Minutes"].sum()
        c  = t[t["Position"] == "Center"]["Projected_Minutes"].sum()
        print(f"{team:<6} {g:>6.1f} {fw:>6.1f} {c:>6.1f} {g+fw+c:>7.1f}")

    return output


if __name__ == "__main__":
    injuries_file = None
    if "--injuries" in sys.argv:
        idx = sys.argv.index("--injuries")
        injuries_file = sys.argv[idx + 1]
    compute_projected_minutes(injuries_file)
