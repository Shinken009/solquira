"""Verify all stored engine examples before checking/updating their source hash.

--update-hash changes only engineSha256; a formula or fixture mismatch blocks it.
No model, network, source formula or stored evaluation is changed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('valle', 'secano', 'precordillera')
HISTORIES = ('cereal', 'leguminosa', 'cubierta', 'descanso')
WATER = range(40, 121, 10)


def verify_evaluations(data, evaluate):
    baseline = {f'{p}:{w}' for p in PROFILES for w in WATER}
    history = {f'{p}:{w}:{h}' for p in PROFILES for w in WATER for h in HISTORIES}
    if set(data['evaluations']) != baseline or set(data['historyEvaluations']) != history:
        raise ValueError('The complete baseline/history fixture matrix is required.')
    def project(actual, template):
        if isinstance(template, dict):
            if not isinstance(actual, dict): raise ValueError('Fixture object shape changed.')
            return {key: project(actual.get(key), value) for key, value in template.items()}
        if isinstance(template, list):
            if not isinstance(actual, list) or len(actual) != len(template): raise ValueError('Fixture list shape changed.')
            return [project(a, b) for a, b in zip(actual, template)]
        return actual
    count = 0
    for group in ('evaluations', 'historyEvaluations'):
        for key, expected in data[group].items():
            parts = key.split(':')
            parcel = copy.deepcopy(data['parcels'][parts[0]])
            if len(parts) == 3:
                parcel['previousCrops'] = [parts[2]]
            actual = evaluate(parcel, int(parts[1]))
            # History snapshots intentionally contain fewer explanatory fields;
            # every stored key/value is still compared, not only chosen numbers.
            projection = project(actual, expected)
            if projection != expected:
                raise ValueError('Engine output differs from stored fixture: ' + key)
            count += 1
    return count


def check(root=ROOT, *, update_hash=False, evaluator=None):
    root = Path(root).resolve(strict=True)
    engine_path = root / 'artifacts/solquira/backend/main.py'
    fixture_path = root / 'artifacts/solquira/src/studio/fixtures.json'
    engine_bytes = engine_path.read_bytes()
    digest = hashlib.sha256(engine_bytes).hexdigest()
    raw = fixture_path.read_bytes()
    data = json.loads(raw)
    if evaluator is None:
        original = list(sys.path)
        module_name = '_solquira_fixture_engine'
        previous = sys.modules.get(module_name)
        try:
            sys.path.insert(0, str(engine_path.parent))
            spec = importlib.util.spec_from_file_location(module_name, engine_path)
            engine = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = engine
            spec.loader.exec_module(engine)
            evaluator = lambda parcel, water: engine.evaluate(engine.EvaluationInput(
                parcel=engine.Parcel.model_validate(parcel), waterPercent=water))
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('Fixture checks forbid network')):
                count = verify_evaluations(data, evaluator)
        finally:
            sys.path[:] = original
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
    else:
        count = verify_evaluations(data, evaluator)
    if engine_path.read_bytes() != engine_bytes or fixture_path.read_bytes() != raw:
        raise ValueError('Source or fixtures changed during verification; retry after edits finish.')
    changed = data.get('engineSha256') != digest
    if changed and not update_hash:
        raise ValueError('The fixture engineSha256 is stale; --update-hash requires matching evaluations.')
    if changed:
        # Replace only the hash bytes, preserving every other source byte.
        pattern = rb'("engineSha256"\s*:\s*")[a-fA-F0-9]{64}(")'
        replacement, matches = re.subn(pattern, lambda m: m[1] + digest.encode('ascii') + m[2], raw)
        if matches != 1:
            raise ValueError('A single recognizable engine hash is required.')
        fixture_path.write_bytes(replacement)
        after = json.loads(fixture_path.read_bytes())
        if {k: v for k, v in after.items() if k != 'engineSha256'} != {k: v for k, v in data.items() if k != 'engineSha256'}:
            raise ValueError('Fixture values changed unexpectedly.')
    return {'status': 'PASS', 'evaluations': count, 'engine_sha256': digest, 'hash_updated': changed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update-hash', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(check(update_hash=args.update_hash)))
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
