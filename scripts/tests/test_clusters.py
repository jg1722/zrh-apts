from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import clusters  # noqa: E402

# Real-world template: the 8810 Horgen spam batch, permuting rooms + price while
# the description stays identical.
HORGEN_BLURB = (
    "Deutsch:\n\n**Helle Wohnung in naturnaher Lage mit Blick Richtung "
    "Zuerichsee**\n\nWir vermieten diese Wohnung mit Seesicht und guter Anbindung."
)


def _horgen(pk: str, rooms: float, rent: int) -> dict:
    return {"id": pk, "blurb": HORGEN_BLURB, "rooms": rooms, "rent_gross": rent}


class ClusterTests(unittest.TestCase):
    def test_flags_group_at_or_above_min_size(self):
        rows = [_horgen(f"f{i}", 3 + i, 1600 + i * 100) for i in range(4)]
        clusters.annotate(rows, min_size=4)
        self.assertTrue(all(r["cluster_size"] == 4 for r in rows))

    def test_digit_and_punct_permutations_do_not_split_cluster(self):
        # Varying room/price digits must not break the shared signature.
        rows = [_horgen("a", 2, 1590), _horgen("b", 4, 1890),
                _horgen("c", 5, 2700), _horgen("d", 6, 2820)]
        clusters.annotate(rows, min_size=4)
        self.assertEqual({r["cluster_size"] for r in rows}, {4})

    def test_below_min_size_not_flagged(self):
        rows = [_horgen("a", 3, 1600), _horgen("b", 4, 1700)]
        clusters.annotate(rows, min_size=4)
        self.assertFalse(any("cluster_size" in r for r in rows))

    def test_distinct_listings_untouched(self):
        rows = [
            {"id": "x", "blurb": "Charmante Altbauwohnung im Kreis 4 mit Stuck."},
            {"id": "y", "blurb": "Moderne Neubauwohnung in Oerlikon mit Loggia."},
        ] + [_horgen(f"h{i}", 3, 1600) for i in range(4)]
        clusters.annotate(rows, min_size=4)
        self.assertNotIn("cluster_size", rows[0])
        self.assertNotIn("cluster_size", rows[1])
        self.assertEqual(rows[2]["cluster_size"], 4)

    def test_short_blurbs_never_cluster(self):
        rows = [{"id": str(i), "blurb": "ab sofort"} for i in range(6)]
        clusters.annotate(rows, min_size=4)
        self.assertFalse(any("cluster_size" in r for r in rows))

    def test_min_size_zero_disables(self):
        rows = [_horgen(f"f{i}", 3, 1600) for i in range(5)]
        clusters.annotate(rows, min_size=0)
        self.assertFalse(any("cluster_size" in r for r in rows))


if __name__ == "__main__":
    unittest.main()
