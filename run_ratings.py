#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
▶ ΤΡΕΧΕΙΣ ΑΥΤΟ (1ο) — βαθμολογεί όλους τους παίκτες.

Αυτό είναι απλώς ο "διακόπτης". Η πραγματική λογική βρίσκεται στο:
    ratings/build_ratings.py

Χρήση:
    python run_ratings.py --round 1                    # αυτόματα fixtures + ratings
    python run_ratings.py --rounds                     # δες τις αγωνιστικές
    python run_ratings.py --setup                      # χειροκίνητα fixtures
    python run_ratings.py --import-costs               # εισαγωγή κόστους
    python run_ratings.py [--sort avg|max|value|value_max]
"""

from ratings import build_ratings

if __name__ == "__main__":
    build_ratings.main()
