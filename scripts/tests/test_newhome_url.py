#!/usr/bin/env python3
"""Tests for newhome detail-URL construction.

Run: .venv/bin/python scripts/tests/test_newhome_url.py

newhome's real SEO detail route (confirmed against a Google-indexed listing) is:
  /de/mieten/immobilien/{type}/{subtype}/ort-{city}/{rooms}-zimmer/detail/{immocode}
The segment that actually selects the listing is /detail/{immocode}; the slug
ahead of it is SEO-descriptive. The old /de/mieten/details/{immocode} 404s.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib.text import _NEWHOME_DETAIL_QUERY, newhome_detail_url  # noqa: E402


class NewhomeUrl(unittest.TestCase):
    def test_matches_confirmed_pattern(self):
        # Path must match the user-confirmed live URL for 6113804; the query is a
        # fixed required suffix (see _NEWHOME_DETAIL_QUERY).
        u = newhome_detail_url("6113804", city="Zürich", rooms=2.5)
        self.assertEqual(
            u,
            "https://www.newhome.ch/de/mieten/immobilien/wohnung/wohnung/"
            "ort-zuerich/2.5-zimmer/detail/6113804" + _NEWHOME_DETAIL_QUERY,
        )

    def test_subtype_and_city_from_photo_slug(self):
        # The canonical type/subtype/city slug is enforced by newhome; derive it
        # from the listing's image URL. 6108869 is a maisonette in Zürich.
        photo = ("https://www.newhome.ch/res/6108869/ort-zuerich/baslerstrasse/"
                 "wohnung/maisonettewohnung-123456-o.jpg")
        u = newhome_detail_url("6108869", city="Zürich", rooms=4.5, photo_url=photo)
        self.assertEqual(
            u,
            "https://www.newhome.ch/de/mieten/immobilien/wohnung/maisonettewohnung/"
            "ort-zuerich/4.5-zimmer/detail/6108869" + _NEWHOME_DETAIL_QUERY,
        )

    def test_photo_slug_overrides_city_for_special_spellings(self):
        photo = ("https://www.newhome.ch/res/1/ort-duebendorf/schulweg/"
                 "wohnung/wohnung-99-o.jpg")
        u = newhome_detail_url("1", city="Dübendorf", rooms=3, photo_url=photo)
        self.assertIn("/wohnung/wohnung/ort-duebendorf/3-zimmer/detail/1", u)

    def test_unparseable_photo_falls_back_to_city_rooms(self):
        u = newhome_detail_url("1", city="Zürich", rooms=3, photo_url="https://x/y.jpg")
        self.assertIn("/wohnung/wohnung/ort-zuerich/3-zimmer/detail/1", u)

    def test_every_url_carries_required_query(self):
        u = newhome_detail_url("123", city="Zürich", rooms=3)
        self.assertIn("?propertyType=2&offerType=2", u)
        self.assertTrue(u.endswith(_NEWHOME_DETAIL_QUERY))

    def test_umlaut_transliteration_de_style(self):
        # German convention: ü→ue, ö→oe, ä→ae (not bare accent-strip)
        self.assertIn("ort-duebendorf", newhome_detail_url("1", city="Dübendorf", rooms=3))
        self.assertIn("ort-zuerich", newhome_detail_url("1", city="Zürich", rooms=2))

    def test_whole_rooms_drop_trailing_zero(self):
        self.assertIn("/3-zimmer/", newhome_detail_url("1", city="Zürich", rooms=3.0))
        self.assertIn("/2.5-zimmer/", newhome_detail_url("1", city="Zürich", rooms=2.5))

    def test_multiword_city_slugified(self):
        self.assertIn("ort-birmensdorf-zh",
                      newhome_detail_url("1", city="Birmensdorf ZH", rooms=2.5))

    def test_subtype_override(self):
        u = newhome_detail_url("9", city="Zürich", rooms=4.5, subtype="attikawohnung")
        self.assertIn("/wohnung/attikawohnung/", u)

    def test_missing_immocode_returns_none(self):
        self.assertIsNone(newhome_detail_url(None, city="Zürich", rooms=3))
        self.assertIsNone(newhome_detail_url("", city="Zürich", rooms=3))

    def test_missing_rooms_still_well_formed(self):
        u = newhome_detail_url("123", city="Zürich", rooms=None)
        self.assertIn("/detail/123" + _NEWHOME_DETAIL_QUERY, u)
        self.assertIn("/de/mieten/immobilien/wohnung/wohnung/ort-zuerich/", u)

    def test_missing_city_falls_back(self):
        u = newhome_detail_url("123", city=None, rooms=3)
        self.assertIn("/detail/123" + _NEWHOME_DETAIL_QUERY, u)
        self.assertIn("/ort-", u)


if __name__ == "__main__":
    unittest.main(verbosity=2)
