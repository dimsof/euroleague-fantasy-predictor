#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
▶ ΤΡΕΧΕΙΣ ΑΥΤΟ (2ο) — βρίσκει τα 2 βέλτιστα σενάρια μεταγραφών.

Ο "διακόπτης". Η λογική βρίσκεται στο:
    team_selection/transfer_search.py   (εξαντλητική αναζήτηση, Pareto pruning)

Χρήση:
    python run_transfers.py --csv ratings.csv --roster my_team.csv \
                            --bank 3.5 --max-transfers 3
"""

from team_selection import transfer_search

if __name__ == "__main__":
    transfer_search.main()
