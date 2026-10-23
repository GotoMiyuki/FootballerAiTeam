"""Compare isolated decrypted BAL snapshots without assigning match semantics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from game_adapters.pes2021_save import RAW_ROOT, SaveError, parse_bal_snapshot, read_copy


def byte_changes(before: bytes, after: bytes, *, sample_limit=64):
    """Exact fixed-offset comparison, bounded samples; insertions are not aligned."""
    if not 0 <= sample_limit <= 256:
        raise SaveError("Diff sample limit must be between 0 and 256")
    common = min(len(before), len(after))
    changed = runs = 0
    run_start = None
    samples = []
    windows = []

    def finish(end):
        nonlocal runs, run_start
        if run_start is not None:
            runs += 1
            if len(samples) < sample_limit:
                samples.append({"start": run_start, "end_exclusive": end,
                                "length": end - run_start,
                                "before_hex_prefix": before[run_start:min(end, run_start + 16)].hex(),
                                "after_hex_prefix": after[run_start:min(end, run_start + 16)].hex()})
            run_start = None

    for start in range(0, common, 4096):
        end = min(common, start + 4096)
        old, new = before[start:end], after[start:end]
        if old == new:
            finish(start)
            continue
        window_count = 0
        for index, (left, right) in enumerate(zip(old, new), start):
            if left != right:
                changed += 1
                window_count += 1
                if run_start is None:
                    run_start = index
            else:
                finish(index)
        windows.append({"start": start, "end_exclusive": end, "changed_bytes": window_count})
    finish(common)
    return {"comparison": "fixed_offsets_without_insertion_alignment",
            "before_size": len(before), "after_size": len(after),
            "common_length": common, "changed_bytes_in_common": changed,
            "before_unpaired_tail_bytes": len(before) - common,
            "after_unpaired_tail_bytes": len(after) - common,
            "changed_run_count": runs, "run_samples": samples,
            "run_samples_truncated": runs > len(samples),
            "changed_window_count": len(windows),
            "most_changed_windows": sorted(windows, key=lambda item: (-item["changed_bytes"], item["start"]))[:sample_limit],
            "semantic_validation": "unknown; changes do not identify match records"}


def compare_decoded(before_meta, before_blocks, after_meta, after_blocks):
    before = parse_bal_snapshot(before_meta, before_blocks)
    after = parse_bal_snapshot(after_meta, after_blocks)
    if (before["native_player_id"], before["identity"]["name"]) != (after["native_player_id"], after["identity"]["name"]):
        raise SaveError("Different target player; refusing to compare as one player")
    if set(before_blocks) != set(after_blocks):
        raise SaveError("Different container block sets")
    attribute_changes = {key: {"before": value["value"], "after": after["attributes"][key]["value"],
                               "validation": value["validation"]}
                         for key, value in before["attributes"].items()
                         if value["value"] != after["attributes"][key]["value"]}
    return {"status": "experimental_native_diff", "match_records_verified": False,
            "target": {"native_player_id": before["native_player_id"], "name": before["identity"]["name"],
                       "career_binding": "unverified; matching name/ID does not prove one career branch"},
            "before": before, "after": after,
            "saved_date_order": "nondecreasing" if after["saved_game_date"] >= before["saved_game_date"] else "reversed",
            "player_record_offset_changed": before["player_data_offset"] != after["player_data_offset"],
            "candidate_attribute_changes": attribute_changes,
            "blocks": {name: byte_changes(before_blocks[name], after_blocks[name]) for name in sorted(before_blocks)},
            "unknown_fields": {"personal_match_records": "No verified match layout; byte differences remain research evidence",
                               "cause_of_changes": "Cannot isolate match progression from other save/game changes"},
            "application_writes": 0, "game_writes": 0}


def compare_copies(before_path: Path, after_path: Path):
    before_meta, before_blocks = read_copy(before_path)
    after_meta, after_blocks = read_copy(after_path)
    report = compare_decoded(before_meta, before_blocks, after_meta, after_blocks)
    report["sources"] = {"before": before_meta, "after": after_meta}
    report["same_source_hash"] = before_meta["source_sha256"] == after_meta["source_sha256"]
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, help="New JSON file inside raw/real_test")
    args = parser.parse_args()
    try:
        if args.output is not None:
            output = args.output.resolve()
            if not output.is_relative_to(RAW_ROOT.resolve()) or output.exists() or not output.parent.is_dir():
                raise SaveError("Output must be a new file under an existing raw/real_test directory")
        report = compare_copies(args.before, args.after)
        payload = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output is not None:
            with output.open("x", encoding="utf-8") as file:
                file.write(payload)
        print(payload)
    except (OSError, SaveError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
