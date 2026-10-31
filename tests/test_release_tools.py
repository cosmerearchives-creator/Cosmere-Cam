"""Regression for the first real draft publication on GitHub."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_release import release_for_version
from update_core import API


class DraftReleaseTests(unittest.TestCase):
    def test_draft_without_a_created_tag_is_verified_from_collection(self):
        client = Mock()
        draft = {'id': 123, 'draft': True, 'tag_name': 'v0.5.0-alpha17', 'assets': []}
        client.json.return_value = [{'tag_name': 'v0.5.0-alpha16.2'}, draft]
        self.assertEqual(release_for_version(client, '0.5.0-alpha17'), draft)
        client.json.assert_called_once_with(API + '/releases?per_page=30')

    def test_missing_release_cannot_skip_verification(self):
        client = Mock()
        client.json.return_value = []
        with self.assertRaises(RuntimeError):
            release_for_version(client, '0.5.0-alpha17')

