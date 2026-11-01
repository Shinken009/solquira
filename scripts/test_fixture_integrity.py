import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fixture_check', Path(__file__).with_name('check-fixtures.py'))
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


class FixtureIntegrityTests(unittest.TestCase):
    def fixture(self, root):
        engine = root / 'artifacts/solquira/backend/main.py'; engine.parent.mkdir(parents=True)
        engine.write_bytes(b'# synthetic test engine\n')
        source = check.ROOT / 'artifacts/solquira/src/studio/fixtures.json'
        target = root / 'artifacts/solquira/src/studio/fixtures.json'; target.parent.mkdir(parents=True)
        data = json.loads(source.read_bytes()); data['engineSha256'] = '0' * 64
        target.write_text(json.dumps(data), encoding='utf8')
        return engine, target

    def test_hash_update_changes_only_hash_after_evaluations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); engine, fixture = self.fixture(root); before = json.loads(fixture.read_bytes())
            with patch.object(check, 'verify_evaluations', return_value=135) as verified:
                result = check.check(root, update_hash=True, evaluator=lambda *a: None)
            self.assertEqual(result['evaluations'], 135); verified.assert_called_once()
            after = json.loads(fixture.read_bytes()); self.assertEqual(after.pop('engineSha256'), hashlib.sha256(engine.read_bytes()).hexdigest())
            before.pop('engineSha256'); self.assertEqual(before, after)

    def test_mismatch_never_updates_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, fixture = self.fixture(root); before = fixture.read_bytes()
            with self.assertRaises(ValueError): check.check(root, update_hash=True, evaluator=lambda *a: {})
            self.assertEqual(before, fixture.read_bytes())

    def test_incomplete_matrix_is_rejected(self):
        data = json.loads((check.ROOT / 'artifacts/solquira/src/studio/fixtures.json').read_bytes())
        data['historyEvaluations'].pop(next(iter(data['historyEvaluations'])))
        with self.assertRaises(ValueError): check.verify_evaluations(data, lambda *a: {})

    def test_current_complete_matrix_matches_engine(self):
        self.assertEqual(check.check()['evaluations'], 135)


if __name__ == '__main__': unittest.main()
