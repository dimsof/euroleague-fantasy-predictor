# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════
 fetch.py — ΚΑΤΕΒΑΖΩ ΤΙΣ ΠΛΗΡΟΦΟΡΙΕΣ ΑΠΟ ΤΟ ΙΝΤΕΡΝΕΤ (ανά ματς)
════════════════════════════════════════════════════════════════════
ΤΙ ΚΑΝΕΙ:
  Για κάθε ματς μιας σεζόν (ή ενός συγκεκριμένου γύρου) φτιάχνει
  ΔΥΟ αρχεία μέσα στο data/matches/:

    S{σεζόν}_R{γύρος}_G{gamecode}_{HOME}v{AWAY}_players.csv
        → μία γραμμή ανά παίκτη (όλα τα στατιστικά — ίδια μορφή με boxscores)

    S{σεζόν}_R{γύρος}_G{gamecode}_{HOME}v{AWAY}_team.csv
        → μία γραμμή ανά ομάδα (τα σύνολα της ομάδας στο ματς)

ΠΗΓΗ: το επίσημο EuroLeague API (μέσω euroleague-api).
      Χτυπάει με ΠΑΥΣΕΙΣ + backoff για να μη φάει rate-limit (429).

ΤΡΕΧΕΙΣ (μέσω run_update.py):
    python run_update.py --season 2025            # όλη η σεζόν ως τώρα
    python run_update.py --season 2025 --round 5  # μόνο ο γύρος 5
"""

import os
import time

import config


# οι μη-παίκτες γραμμές που πετάμε
_NON_PLAYERS = {"Team", "Total"}


def _safe_team(name):
    """Καθαρίζει το όνομα ομάδας για χρήση σε filename."""
    return "".join(c for c in str(name) if c.isalnum()) or "UNK"


def _is_429(msg):
    return "429" in msg or "Too Many" in msg


def _get_season_gamecodes(bx, season, max_tries=8):
    """ΜΙΑ κλήση: όλη η λίστα αγώνων της σεζόν (gameCode, Round, Phase, played)."""
    wait = 5.0
    for attempt in range(max_tries):
        try:
            return bx.get_gamecodes_season(season)
        except Exception as ex:
            if _is_429(str(ex)):
                print(f"  λίστα σεζόν: 429, περιμένω {wait:.0f}s ({attempt+1}/{max_tries})")
                time.sleep(wait)
                wait = min(wait * 1.7, 90)
            else:
                raise
    return None


def _fetch_one_game(bx, season, gamecode, phase, rnd, max_tries=6):
    """
    Κατεβάζει ΕΝΑ παιχνίδι με backoff στα 429.
    Επιστρέφει DataFrame (με Phase/Round), ή 'EMPTY' (δεν έπαιξε), ή 'FAILED'.
    """
    wait = 5.0
    for attempt in range(max_tries):
        try:
            df = bx.get_players_boxscore_stats(season, gamecode)
            if df is None or df.empty:
                return "EMPTY"
            # πρόσθεσε Phase/Round ώστε η μορφή να ταιριάζει ΑΚΡΙΒΩΣ με τα υπάρχοντα
            if "Phase" not in df.columns and phase is not None:
                df.insert(1, "Phase", phase)
            if "Round" not in df.columns and rnd is not None:
                df.insert(2, "Round", rnd)
            return df
        except Exception as ex:
            msg = str(ex)
            if _is_429(msg):
                time.sleep(wait)
                wait = min(wait * 1.7, 90)
            elif "not find" in msg.lower() or "404" in msg:
                return "EMPTY"
            else:
                time.sleep(3)
    return "FAILED"


def _write_match_files(match_df, season):
    """Γράφει τα 2 αρχεία (players + team) για ΕΝΑ ματς."""
    os.makedirs(config.MATCHES_DIR, exist_ok=True)

    rnd = int(match_df["Round"].iloc[0]) if "Round" in match_df.columns else 0
    gamecode = int(match_df["Gamecode"].iloc[0])

    # ποιος είναι εντός (Home==1) / εκτός
    teams = match_df.groupby("Team")["Home"].first()
    home = teams[teams == 1].index[0] if (teams == 1).any() else teams.index[0]
    away = teams[teams != teams.loc[home]].index
    away = away[0] if len(away) else "UNK"
    tag = f"S{season}_R{rnd:02d}_G{gamecode}_{_safe_team(home)}v{_safe_team(away)}"

    # 1) players — όπως έρχεται (ίδια μορφή με boxscores)
    players = match_df[~match_df["Player"].isin(_NON_PLAYERS)].copy()
    players.to_csv(os.path.join(config.MATCHES_DIR, tag + "_players.csv"), index=False)

    # 2) team totals — άθροισμα αριθμητικών στηλών ανά ομάδα
    numeric = players.select_dtypes("number").columns
    keep = [c for c in numeric if c not in ("Season", "Round", "Gamecode",
                                            "Home", "Player_ID", "Dorsal")]
    team = players.groupby("Team")[keep].sum().reset_index()
    team.insert(0, "Season", season)
    team.insert(1, "Round", rnd)
    team.insert(2, "Gamecode", gamecode)
    team.to_csv(os.path.join(config.MATCHES_DIR, tag + "_team.csv"), index=False)

    return tag


def _have_gamecode(season, gamecode):
    """True αν το συγκεκριμένο παιχνίδι έχει ήδη κατέβει (per-match αρχείο)."""
    import glob
    pat = os.path.join(config.MATCHES_DIR, f"S{season}_R*_G{gamecode}_*_players.csv")
    return len(glob.glob(pat)) > 0


def fetch_season(season, only_round=None, pause=6.0, force=False, cooldown=60.0):
    """
    Κατεβάζει τα ματς μιας σεζόν, ΕΝΑ-ΕΝΑ με σταθερή παύση, και γράφει
    τα per-match αρχεία.

    ΓΙΑΤΙ ΕΝΑ-ΕΝΑ: το EuroLeague API έχει burst-limit. Η παλιά μέθοδος
    "ανά γύρο" κατέβαζε 10 παιχνίδια σε ριπή (6-14/δευτ.) και προκαλούσε
    429. Εδώ κατεβάζουμε 1 παιχνίδι κάθε `pause` δευτ. → σταθερός ρυθμός,
    ελάχιστα 429.

    only_round : αν δοθεί, μόνο τα παιχνίδια αυτού του γύρου.
    force      : ξανακατεβάζει ΚΑΙ όσα παιχνίδια υπάρχουν ήδη.
    pause      : δευτ. παύσης ΑΝΑ ΠΑΙΧΝΙΔΙ (μεγαλύτερο = ασφαλέστερο).
    cooldown   : μεγάλη παύση όταν ένα παιχνίδι αποτύχει τελείως (429-τιμωρία).

    ΑΝΘΕΚΤΙΚΟ:
      • Προσπερνά παιχνίδια που ΗΔΗ κατέβηκαν → το re-run συμπληρώνει μόνο κενά.
      • Retry στα παιχνίδια που έφαγαν 429, σε 3 πάσα με cooldown.
    """
    from euroleague_api.boxscore_data import BoxScoreData
    bx = BoxScoreData()

    print(f"→ Παίρνω τη λίστα αγώνων της σεζόν {season} (1 κλήση)...")
    gc = _get_season_gamecodes(bx, season)
    if gc is None:
        raise RuntimeError("Δεν κατέβηκε η λίστα αγώνων (rate-limit) — ξαναδοκίμασε.")
    gc = gc[gc["played"]].copy()
    if only_round is not None:
        gc = gc[gc["Round"] == only_round]
    total = len(gc)
    print(f"  {total} παιγμένοι αγώνες στο πρόγραμμα.  Παύση {pause:.0f}s/παιχνίδι.\n")

    def _do(row):
        """Κατεβάζει & γράφει ένα παιχνίδι. Επιστρέφει 'OK'/'SKIP'/'EMPTY'/'FAILED'."""
        gcode = int(row["gameCode"])
        rnd = int(row["Round"]) if "Round" in row else 0
        phase = row.get("Phase") if hasattr(row, "get") else row["Phase"]
        if not force and _have_gamecode(season, gcode):
            return "SKIP"
        res = _fetch_one_game(bx, season, gcode, phase, rnd)
        if isinstance(res, str):
            return res                       # EMPTY / FAILED
        tag = _write_match_files(res, season)
        print(f"   ✓ {tag}")
        return "OK"

    got, skipped, failed = 0, 0, []
    for _, row in gc.iterrows():
        r = _do(row)
        if r == "OK":
            got += 1
            time.sleep(pause)               # σταθερή παύση — ΟΧΙ ριπή
        elif r == "SKIP":
            skipped += 1
        elif r == "FAILED":
            print(f"   παιχνίδι {int(row['gameCode'])}: ✗ 429 — για retry")
            failed.append(row)
            time.sleep(cooldown)            # τιμωρία 429 → μεγάλη ανάσα

    # ── retry passes για τα αποτυχημένα ──
    for attempt in range(3):
        if not failed:
            break
        print(f"\n↻ Retry {len(failed)} παιχνιδιών (πάσο {attempt+1}/3)...")
        time.sleep(cooldown)
        still = []
        for row in failed:
            r = _do(row)
            if r == "OK":
                got += 1
                time.sleep(pause)
            elif r == "FAILED":
                still.append(row)
        failed = still

    if skipped:
        print(f"   (προσπεράστηκαν {skipped} παιχνίδια που ήταν ήδη κατεβασμένα)")
    print(f"\n✓ Νέα ματς αυτή τη φορά: {got} στο {config.MATCHES_DIR}")
    downloaded = sum(1 for _, row in gc.iterrows() if _have_gamecode(season, int(row["gameCode"])))
    print(f"✓ Σύνολο διαθέσιμων αγώνων: {downloaded}/{total}")
    if downloaded < total:
        miss = total - downloaded
        print(f"⚠ Λείπουν ακόμα {miss} αγώνες.")
        print("  → ξανα-τρέξε `python run_update.py` (συμπληρώνει ΜΟΝΟ τα κενά).")
        print("  → αν επιμένει, δοκίμασε πιο αργά: `python run_update.py --pause 12`")
    else:
        print("✓ Όλοι οι αγώνες της σεζόν κατεβασμένοι!")
    return got
