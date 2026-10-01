# -*- coding: utf-8 -*-
"""
Πακέτο ingestion — συλλογή δεδομένων από το EuroLeague API.

  schedule.py  → κατεβάζει το πρόγραμμα της σεζόν (όλα τα ματς)
  fetch.py     → κατεβάζει, για κάθε ματς, 2 αρχεία στο data/matches/:
                   ..._players.csv  (στατιστικά παικτών)
                   ..._team.csv     (σύνολα ομάδων)
  store.py     → ενσωματώνει τα per-match players στο ετήσιο boxscores.csv

⚠ Τρέχει στο PC σου (χρειάζεται internet + το πακέτο euroleague-api).
"""
