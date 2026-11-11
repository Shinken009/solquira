"""Rebuild the offline WCFP v2 crop reference from its verified Figshare archive."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1] / "artifacts/solquira/src/studio"
SOURCE_SHA256 = "0455d7523e65ec00a0e08546533f3976663455b7bc1f20c32c4a998ac174665f"


def derive(source):
    digest = hashlib.sha256()
    with source.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != SOURCE_SHA256:
        raise ValueError("Expected the exact WCFP v2 Figshare archive")
    aliases = json.loads((ROOT / "crop-aliases.json").read_text(encoding="utf-8"))
    entries = []
    with ZipFile(source) as archive, archive.open("Code/input/WCFP_final.csv") as file:
        for row in csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig", newline="")):
            name = row["taxon_name_accepted"]
            if row["cultivated_GRIN"] == "1" or name in aliases:
                family = row["ALL_families"]
                if not name or not family or family == "NA":
                    raise ValueError(f"Incomplete reference: {row['WCFP_ID']}")
                entries.append((int(row["WCFP_ID"]), name, family))
    if len({row[0] for row in entries}) != len(entries):
        raise ValueError("Duplicate WCFP IDs")
    missing = set(aliases) - {row[1] for row in entries}
    if missing:
        raise ValueError(f"Aliases without a WCFP v2 row: {sorted(missing)}")
    return "[\n" + ",\n".join(
        json.dumps(row, ensure_ascii=False) for row in sorted(entries, key=lambda row: (row[1], row[0]))
    ) + "\n]\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = derive(args.archive).encode()
    target = ROOT / "crop-reference.wcfp-v2.json"
    if args.check:
        if target.read_bytes() != data:
            parser.exit(1, "Committed crop reference differs from regenerated data\n")
    else:
        target.write_bytes(data)
    print(f"PASS: {len(json.loads(data))} WCFP v2 entries")


if __name__ == "__main__":
    main()
