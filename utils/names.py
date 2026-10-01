# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════
 names.py — ΑΝΑΓΝΩΣΗ & ΤΑΙΡΙΑΣΜΑ ΟΝΟΜΑΤΩΝ
════════════════════════════════════════════════════════════════════
ΤΟ ΠΡΟΒΛΗΜΑ ΠΟΥ ΛΥΝΕΙ:
  Τα ονόματα στα box scores έχουν κόμμα ("JAMES, MIKE"), που σπάει
  τα CSV. Και εσύ θέλεις να γράφεις απλά "JAMES" χωρίς να ψάχνεις
  την ακριβή γραφή.

ΤΙ ΚΑΝΕΙ:
  • Διαβάζει λίστες ονομάτων από αρχείο, γραμμή-γραμμή (δεν σπάει στο κόμμα)
  • Ταιριάζει "χαλαρά": αρκεί ό,τι έγραψες να υπάρχει μέσα στο πλήρες όνομα
      "LARKIN"        ταιριάζει με  "LARKIN, SHANE"   ✓
      "LARKIN, SHANE" ταιριάζει με  "LARKIN, SHANE"   ✓
      "SHANE LARKIN"  ταιριάζει με  "LARKIN, SHANE"   ✓  (η σειρά δεν πειράζει)
"""

import os
import unicodedata


def normalize(text):
    """Σπάει ένα όνομα σε λέξεις-κλειδιά: κεφαλαία, χωρίς τόνους/κόμματα."""
    s = str(text).upper()
    # πέτα τόνους (π.χ. Š -> S) ώστε να ταιριάζουν και ξένα ονόματα
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    for ch in ",.;:-_'\"":
        s = s.replace(ch, " ")
    return set(s.split())


def read_name_list(path):
    """
    Διαβάζει ονόματα από αρχείο — ΕΝΑ ανά γραμμή.
    Αγνοεί: σχόλια (#), κενές γραμμές, και την επικεφαλίδα 'player'.
    Δεν χρησιμοποιεί CSV parser, γι' αυτό δεν σπάει στα κόμματα.
    """
    if not os.path.exists(path):
        return []
    names = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip().strip('"').strip()
            if not s or s.startswith("#") or s.lower() == "player":
                continue
            names.append(s)
    return names


def matches(written_name, full_name):
    """True αν αυτό που έγραψε ο χρήστης ταιριάζει με το πλήρες όνομα."""
    written = normalize(written_name)
    return bool(written) and written.issubset(normalize(full_name))


def find_players(written_names, all_names):
    """
    Ταιριάζει μια λίστα γραμμένων ονομάτων με τα πραγματικά ονόματα.

    Επιστρέφει:  (βρέθηκαν, δεν_βρέθηκαν)
      βρέθηκαν     = λίστα με τα ΠΛΗΡΗ ονόματα που ταίριαξαν
      δεν_βρέθηκαν = λίστα με ό,τι έγραψες και δεν βρέθηκε
    """
    found, missing = [], []
    for written in written_names:
        hit = next((full for full in all_names if matches(written, full)), None)
        if hit:
            found.append(hit)
        else:
            missing.append(written)
    return found, missing
