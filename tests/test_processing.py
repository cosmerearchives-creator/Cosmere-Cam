import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))
from recording_processing import align_script, kept_segments


class ProcessingTests(unittest.TestCase):
    def test_missing_sections_and_punctuation_preserved(self):
        script = 'Before, the ancient lost kingdom returns. Afterwards!'
        result = align_script(script, [dict(word='the', start=1, end=1.2),
            dict(word='kingdom', start=3, end=3.5), dict(word='returns', start=4, end=4.4)], 6)
        words = result['words']
        self.assertEqual([w['word'] for w in words], script.split())
        self.assertTrue(words[0]['needs_approval'])
        self.assertTrue(words[2]['needs_approval'])
        self.assertTrue(words[3]['needs_approval'])
        self.assertFalse(words[4]['needs_approval'])
        self.assertEqual(words[2]['start'], 1.2)
        self.assertAlmostEqual(words[3]['end'], 3)
        for before, after in zip(words, words[1:]):
            self.assertLessEqual(before['end'], after['start'])
        self.assertEqual(words[-1]['end'], 6)

    def test_no_recognized_words_never_drops_script(self):
        words = align_script('all words stay visible', [], 10)['words']
        self.assertEqual(len(words), 4)
        self.assertTrue(all(w['needs_approval'] for w in words))
        self.assertEqual(words[0]['start'], 0)
        self.assertEqual(words[-1]['end'], 10)

    def test_low_confidence_match_requires_approval(self):
        result = align_script('a missing word', [dict(word='missing',start=1,end=2,conf=.1)], 3)
        self.assertTrue(all(w['needs_approval'] for w in result['words']))

    def test_edge_trim_and_internal_pause_modes(self):
        silence = [(0, 1), (3, 5), (9, 10)]
        edges = kept_segments(10, silence)
        pauses = kept_segments(10, silence, True)
        self.assertEqual(edges, [(.85, 9.15)])
        self.assertEqual(pauses, [(.85, 3.15), (4.85, 9.15)])
        self.assertGreater(sum(b-a for a,b in edges), sum(b-a for a,b in pauses))

    def test_silent_take_not_discarded(self):
        self.assertEqual(kept_segments(10, [(0,10)], True), [(0,10)])
