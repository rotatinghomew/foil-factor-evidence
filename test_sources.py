from __future__ import annotations

import shutil
from pathlib import Path
import tempfile
import unittest

from check_sources import load_sources

DATA = Path(__file__).with_name('data')


class ExchangeTests(unittest.TestCase):
    def test_example_is_readable(self):
        tables = load_sources(DATA)
        self.assertTrue(all(tables.values()))
        self.assertEqual({r['run'] for r in tables['results']}, {r['id'] for r in tables['runs']})
        self.assertTrue({r['stage'] for r in tables['runs']} >= {'initial', 'followup'})
        self.assertTrue(all(float(r['seal']) > 0 and r['scrap'] in ('0','1') for r in tables['results']))
        self.assertTrue(any(r['p_assign'] != r['p_actual'] or r['c_assign'] != r['c_actual'] for r in tables['runs']))

    def test_duplicate_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / 'copies'
            shutil.copytree(DATA, duplicate)
            path = duplicate / 'runs.csv'
            rows = path.read_text(encoding='utf-8').splitlines()
            path.write_text('\n'.join(rows + [rows[1]]) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '标识为空或重复'):
                load_sources(duplicate)
