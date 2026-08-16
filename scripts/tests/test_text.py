#!/usr/bin/env python3
"""Unit tests for applib.text helpers — run: .venv/bin/python scripts/tests/test_text.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib.text import (  # noqa: E402
    is_age_restricted_listing, is_exchange_listing, is_room_only_listing,
    lease_term_months, match_hard_temp_keyword)

SYN_ROOM = ["mitbewohner", "wohngemeinschaft", "wg-zimmer", "wg zimmer",
            "zimmer in einer wg", "zimmer zu vermieten", "shared flat",
            "shared apartment", "flatmate"]
SYN_AGE = ["alterswohnung", "seniorenwohnung", "betreutes wohnen",
           "assisted living", "altersresidenz", "mindestalter", "wohnen im alter"]


class ExchangeListing(unittest.TestCase):
    def yes(self, text):
        self.assertTrue(is_exchange_listing(text), f"should flag: {text!r}")

    def no(self, text):
        self.assertFalse(is_exchange_listing(text), f"should NOT flag: {text!r}")

    def test_explicit_compounds_and_phrases(self):
        self.yes("Schöne Tauschwohnung in Zürich")
        self.yes("Melde dich bei Interesse an einem Wohnungstausch!")
        self.yes("Biete meine 4-Zimmer-Wohnung in Wipkingen zum Tausch an")
        self.yes("3.5-Zimmer, Tausch gegen 4.5-Zimmer gesucht")

    def test_non_adjacent_wohnung_tauschen(self):
        # the listing that slipped the old gate (verb ~9 words from "Wohnung")
        self.yes("Willst du deine Wohnung in der Schweiz: Thalwil, Rüschlikon, "
                 "Kilchberg, Wollishofen oder Zürich tauschen?")

    def test_reverse_wir_tauschen_unsere_wohnung(self):
        self.yes("Wir tauschen unsere Altbauwohnung im Kreis 3 gegen etwas grösseres.")
        self.yes("Ich tausche meine 4.5-Zimmer-Wohnung im lebhaften Kreis 9!")

    def test_bare_phrases(self):
        self.yes("Im Tausch suche ich eine Wohnung in Zürich")
        self.yes("Lass uns tauschen!")
        self.yes("Familien, die einen Tausch nach Zürich suchen")

    def test_english_exchange(self):
        self.yes("We are open to exchanges for a slightly bigger apartment in Kreis 5")
        self.yes("Looking to swap our flat for a bigger home")

    def test_renovation_words_not_matched(self):
        self.no("Neuer Fenstertausch und Küchentausch 2024")
        # fixture verb near "Wohnung" must still not trigger the proximity rule
        self.no("Helle Wohnung, wir haben die Fenster tauschen lassen")
        self.no("Wohnung mit Boilertausch und neuer Heizung")

    def test_community_austausch_not_matched(self):
        # "Austausch mit den Nachbarn" = neighbourly exchange, not a flat swap
        self.no("Hier finden Menschen ein Zuhause, die den Austausch mit den "
                "Nachbar:innen schätzen und pflegen wollen")

    def test_normal_listing_not_matched(self):
        self.no("Helle 3.5-Zimmer-Wohnung mit Balkon und Garage")

    def test_empty(self):
        self.no("")
        self.no(None)

    def test_optional_synonyms_extend(self):
        self.assertTrue(is_exchange_listing("Biete Ringtausch an", ["ringtausch"]))


class RoomOnly(unittest.TestCase):
    def yes(self, t): self.assertTrue(is_room_only_listing(t, SYN_ROOM), repr(t))
    def no(self, t): self.assertFalse(is_room_only_listing(t, SYN_ROOM), repr(t))

    def test_flatmate_and_wg(self):
        self.yes("Ab 1. Juli suche ich eine Mitbewohnerin für meine 5.5-Zimmer-Wohnung")
        self.yes("Mitbewohner:in gesucht ab sofort")
        self.yes("Schönes Zimmer in einer WG im Kreis 4")
        self.yes("4er WG, Zimmer frei ab sofort")          # N-person WG
        self.yes("WG-Zimmer, möbliert, zentral")
        self.yes("Bright room in a shared flat near the lake")

    def test_whole_apartment_not_matched(self):
        self.no("Schöne 3.5-Zimmer-Wohnung mit Balkon und Garage")
        self.no("Grosszügige 5.5-Zimmer-Wohnung, 150 m², zentral")
        # whole flats merely advertised as WG-suitable — must NOT be rejected
        self.no("Ideal für Singles, Paare oder eine WG")
        self.no("Grosse 5.5-Zimmer-Wohnung, auch als WG geeignet")
        self.no("3.5-Zimmer-Wohnung zu vermieten (no WG)")

    def test_empty(self):
        self.no(""); self.no(None)


class AgeRestricted(unittest.TestCase):
    def yes(self, t): self.assertTrue(is_age_restricted_listing(t, SYN_AGE), repr(t))
    def no(self, t): self.assertFalse(is_age_restricted_listing(t, SYN_AGE), repr(t))

    def test_age_patterns_and_terms(self):
        self.yes("Wohnen mit Service ab 55 Jahren+")
        self.yes("Altersgerechte Wohnungen für Menschen ab 55 Jahren")
        self.yes("Moderne 55+ Wohnung in Greencity")
        self.yes("Stiftung Alterswohnungen der Stadt Zürich")
        self.yes("Betreutes Wohnen mit Pflegeangebot")

    def test_not_age_false_positives(self):
        self.no("Moderne Wohnung, 60 m², ruhige Lage")        # "60 m²" not an age
        self.no("Altersgerecht ausgebaut, rollstuhlgängig")   # accessibility, not a gate
        self.no("Verfügbar ab 1. Juni 2026")                  # move-in date, not age
        self.no("Helle 3.5-Zimmer-Wohnung mit Balkon")

    def test_empty(self):
        self.no(""); self.no(None)


class LeaseTerm(unittest.TestCase):
    """lease_term_months returns (term_months, has_temp_keyword, is_fixed_term).

    Regression cases come from real listings that leaked into the digest in
    July 2026 (the user's decline notes: "temporary", "should not have passed
    gates"): fixed terms >= lease.min_months passed the gate silently.
    """

    def test_befristet_auf_n_monate_is_fixed(self):
        term, kw, fixed = lease_term_months("Die Wohnung ist befristet auf 12 Monate zu vermieten")
        self.assertEqual(term, 12)
        self.assertTrue(kw)
        self.assertTrue(fixed)

    def test_fuer_n_monate_is_fixed(self):
        term, kw, fixed = lease_term_months("Möbliert für 8 Monate zu vermieten")
        self.assertEqual(term, 8)
        self.assertTrue(fixed)

    def test_date_range_is_fixed(self):
        # newhome-6117926 pattern: "MÖBLIERT, TEMPORÄR" + range ≥ min_months leaked as bucket A
        term, kw, fixed = lease_term_months(
            "2.5 Zi-Whg, MÖBLIERT, TEMPORÄR vom 1.08.2026 bis 30.04.2027")
        self.assertIsNotNone(term)
        self.assertAlmostEqual(term, 8.9, delta=0.3)
        self.assertTrue(kw)
        self.assertTrue(fixed)

    def test_mindestmietdauer_is_minimum_not_fixed(self):
        # A minimum commitment on an open-ended lease is NOT a temporary flat.
        term, kw, fixed = lease_term_months("Mindestmietdauer 12 Monate, unbefristeter Vertrag")
        self.assertEqual(term, 12)
        self.assertFalse(fixed)

    def test_mindestens_is_minimum_not_fixed(self):
        term, kw, fixed = lease_term_months("Vermietung für mindestens 6 Monate")
        self.assertEqual(term, 6)
        self.assertFalse(fixed)

    def test_keyword_only_no_term(self):
        term, kw, fixed = lease_term_months("Schöne Zwischenmiete im Kreis 4")
        self.assertIsNone(term)
        self.assertTrue(kw)
        self.assertFalse(fixed)

    def test_normal_listing(self):
        term, kw, fixed = lease_term_months(
            "Unbefristeter Mietvertrag, kündbar ab 3 Monaten, schöne 3.5-Zimmer-Wohnung")
        self.assertIsNone(term)
        self.assertFalse(kw)
        self.assertFalse(fixed)

    def test_empty(self):
        self.assertEqual(lease_term_months(""), (None, False, False))
        self.assertEqual(lease_term_months(None), (None, False, False))


class HardTempKeyword(unittest.TestCase):
    KWS = ["zwischenmiete", "zwischennutzung", "untermiete", "sublet"]

    def test_matches(self):
        self.assertEqual(match_hard_temp_keyword("Schöne Zwischenmiete ab sofort", self.KWS),
                         "zwischenmiete")
        self.assertEqual(match_hard_temp_keyword("Offered as a sublet", self.KWS), "sublet")
        self.assertEqual(match_hard_temp_keyword("Untermiete für 6 Monate", self.KWS),
                         "untermiete")

    def test_no_match_and_empty(self):
        self.assertIsNone(match_hard_temp_keyword("Normale 3.5-Zimmer-Wohnung", self.KWS))
        self.assertIsNone(match_hard_temp_keyword("", self.KWS))
        self.assertIsNone(match_hard_temp_keyword("Zwischenmiete", []))


if __name__ == "__main__":
    unittest.main()
