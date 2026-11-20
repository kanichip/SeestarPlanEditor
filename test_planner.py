"""Regression checks for Stellarium coordinate import (no network access)."""
import io
import json
import unittest
from unittest.mock import patch

from planner import selected_object


class SelectedObjectTests(unittest.TestCase):
    def fetch(self, ra, dec=44.334750842600656):
        response = io.StringIO(json.dumps({
            'name': 'North America Nebula', 'raJ2000': ra, 'decJ2000': dec,
        }))
        with patch('planner.urlopen', return_value=response):
            return selected_object('http://localhost:8090')

    def test_north_america_nebula_signed_ra(self):
        name, ra, dec = self.fetch(-45.30573441713088)
        self.assertEqual(name, 'North America Nebula')
        self.assertAlmostEqual(ra, 20.97961770552461)
        self.assertEqual(dec, 44.334750842600656)
        self.assertAlmostEqual(ra, self.fetch(314.6942655828691)[1])

    def test_ra_wrap_and_positive_coordinates(self):
        for degrees, hours in [(0, 0), (360, 0), (-360, 0), (90, 6), (-90, 18)]:
            with self.subTest(degrees=degrees):
                self.assertEqual(self.fetch(degrees)[1], hours)

    def test_nonfinite_ra_is_rejected(self):
        for degrees in [float('nan'), float('inf'), float('-inf')]:
            with self.subTest(degrees=degrees), self.assertRaises(ValueError):
                self.fetch(degrees)

    def test_declination_remains_range_checked(self):
        with self.assertRaises(ValueError):
            self.fetch(90, dec=91)


if __name__ == '__main__':
    unittest.main()
