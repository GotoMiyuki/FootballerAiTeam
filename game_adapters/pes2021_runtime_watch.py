"""Bounded, read-only runtime checkpoints; observation segments are not matches.

Offline replay exercises retention logic only. It never counts as live acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from game_adapters.pes2021_runtime_stats import (
    SCHEMA, RuntimeStatsError, WindowsReader, capture_stats,
)
from game_adapters.pes2021_save import RAW_ROOT

MAX_SAMPLE_BYTES = 2 * 1024 * 1024
MANIFEST_RESERVE = 256 * 1024
MAX_EVENTS = 256
VOLATILE = {"captured_at", "collection_seconds", "read_bytes"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False).encode("utf-8")


def new_directory(path, raw_root=RAW_ROOT):
    path, root = Path(path).resolve(), Path(raw_root).resolve()
    if path == root or not path.is_relative_to(root) or path.exists() or not path.parent.is_dir():
        raise RuntimeStatsError("Output must be a new directory inside existing raw/real_test")
    path.mkdir()
    return path


def counter_regressions(previous, current):
    old = {(r["native_player_id"], r["side"], r["roster_slot"]): r for r in previous["records"]}
    regressions = []
    for record in current["records"]:
        prior = old.get((record["native_player_id"], record["side"], record["roster_slot"]))
        if prior:
            for name, value in record["counters"].items():
                if value < prior["counters"].get(name, value):
                    regressions.append({"native_player_id": record["native_player_id"],
                                        "side": record["side"], "roster_slot": record["roster_slot"],
                                        "metric": name, "before": prior["counters"][name], "after": value})
                    if len(regressions) == 8:
                        return regressions
    return regressions


class Recorder:
    """Keep the latest valid sample through absence/errors without certifying it."""

    def __init__(self, output, source_mode, target_id, *, raw_root=RAW_ROOT,
                 checkpoint_seconds=30, max_snapshots=64, max_bytes=32 * 1024 * 1024):
        if source_mode not in ("live_read", "offline_replay"):
            raise RuntimeStatsError("Invalid source mode")
        if not 2 <= max_snapshots <= 128 or not 2 * MAX_SAMPLE_BYTES + MANIFEST_RESERVE <= max_bytes <= 64 * 1024 * 1024:
            raise RuntimeStatsError("Invalid retention budget")
        if not 0 < target_id < 0xFFFFFFFF or not 1 <= checkpoint_seconds <= 600:
            raise RuntimeStatsError("Invalid target or checkpoint interval")
        self.output = new_directory(output, raw_root)
        self.source_mode, self.target_id = source_mode, target_id
        self.checkpoint_seconds, self.max_snapshots, self.max_bytes = checkpoint_seconds, max_snapshots, max_bytes
        self.bytes_written, self.polls, self.segment = 0, 0, 0
        self.last, self.present, self.last_checkpoint = None, False, None
        self.process_instance, self.token = None, None
        self.events, self.snapshots, self.references = [], [], {}
        self.stop_reason, self.finished = None, False
        self.identity_review_needed, self.continuity_uncertain = False, False
        self.available_observations, self.unavailable_observations, self.read_errors = 0, 0, 0
        self.first_unavailable, self.last_unavailable = None, None

    def event(self, kind, elapsed, **details):
        # Reserve one event for final stop; never grow a manifest indefinitely.
        if len(self.events) < MAX_EVENTS - 1:
            self.events.append({"kind": kind, "elapsed_seconds": elapsed,
                                "observation_segment": self.segment, **details})
        else:
            self.stop_reason = "event_budget"

    def persist_last(self, reason, elapsed, *, final=False):
        if self.last is None:
            return None
        report, source = self.last
        digest = hashlib.sha256(encode({k: v for k, v in report.items() if k not in VOLATILE})).hexdigest()
        key = self.segment, digest
        if key in self.references:
            return self.references[key]
        payload = encode({"schema": "pes2021-runtime-checkpoint-experimental-v1",
                          "source_mode": self.source_mode, "source": source,
                          "observation_segment": self.segment,
                          "retention_reason": reason, "runtime_report": report,
                          "match_ready": False, "d2_ready": False})
        if len(payload) > MAX_SAMPLE_BYTES:
            raise RuntimeStatsError("Sample exceeds retention size limit")
        reserve = MANIFEST_RESERVE + (0 if final else MAX_SAMPLE_BYTES)
        count_limit = self.max_snapshots if final else self.max_snapshots - 1
        if len(self.snapshots) >= count_limit or self.bytes_written + len(payload) + reserve > self.max_bytes:
            self.stop_reason = "retention_budget"
            return None
        name = f"sample-{len(self.snapshots) + 1:04d}.json"
        with (self.output / name).open("xb") as stream:
            stream.write(payload)
        self.bytes_written += len(payload)
        reference = {"path": name, "sha256": hashlib.sha256(payload).hexdigest(),
                     "observation_segment": self.segment, "reason": reason,
                     "elapsed_seconds": elapsed, "source_captured_at": report.get("captured_at")}
        self.snapshots.append(reference)
        self.references[key] = name
        self.last_checkpoint = elapsed
        return name

    def accept(self, report, elapsed, source=None):
        if self.finished or self.stop_reason:
            raise RuntimeStatsError("Recorder has stopped")
        if report.get("schema") != SCHEMA or report.get("target_native_player_id") != self.target_id:
            raise RuntimeStatsError("Unsupported sample or target mismatch")
        if report.get("status") not in ("runtime_preview", "unavailable"):
            raise RuntimeStatsError("Unsupported sample status")
        if any(report.get(k, False) is not False for k in ("snapshot_ready", "match_ready", "d2_ready", "event_identity_verified")):
            raise RuntimeStatsError("Experimental samples cannot certify import or match identity")
        if report.get("status") == "unavailable" and report.get("records") is not None:
            raise RuntimeStatsError("Unavailable sample must have null records")
        if report.get("status") == "runtime_preview" and not isinstance(report.get("records"), list):
            raise RuntimeStatsError("Available sample requires records")
        if len(encode(report)) > MAX_SAMPLE_BYTES:
            raise RuntimeStatsError("Sample exceeds retention size limit")
        self.polls += 1
        instance = report.get("process_instance")
        if instance and instance.get("creation_time_100ns") is not None:
            if self.process_instance is not None and instance != self.process_instance:
                self.event("process_changed", elapsed)
                self.stop_reason = "process_changed"
                return
            self.process_instance = instance
        if report["status"] == "unavailable":
            self.unavailable_observations += 1
            summary_keys = ("schema", "profile", "captured_at", "status", "reason",
                            "target_native_player_id", "process_instance", "verified_code_sha256",
                            "read_bytes", "snapshot_ready", "match_ready", "d2_ready", "event_identity_verified")
            summary = {k: report[k] for k in summary_keys if k in report}
            summary.update(elapsed_seconds=elapsed, source=source)
            self.first_unavailable = self.first_unavailable or summary
            self.last_unavailable = summary
            if self.present:
                reference = self.persist_last("before_observed_absence", elapsed)
                self.event("buffers_absent", elapsed, last_valid_checkpoint=reference,
                           completion_verified=False)
            elif self.polls == 1:
                self.event("initially_unavailable", elapsed)
            self.present = False
            return
        self.available_observations += 1
        token = report.get("runtime_instance_token")
        changed = self.present and token is not None and self.token is not None and token != self.token
        if not self.present or changed:
            if self.segment:
                self.identity_review_needed = True
            if changed:
                reference = self.persist_last("before_object_change", elapsed)
                self.event("objects_changed", elapsed, last_valid_checkpoint=reference)
                self.identity_review_needed = True
            self.segment += 1
            self.event("buffers_observed", elapsed, allocation_token_available=token is not None,
                       match_identity_verified=False)
            self.last, self.token, self.present = (report, source), token, True
            self.persist_last("first_observed", elapsed)
            return
        regressions = counter_regressions(self.last[0], report)
        if regressions:
            reference = self.persist_last("before_counter_regression", elapsed)
            self.identity_review_needed = True
            self.event("counter_regression", elapsed, examples=regressions,
                       previous_checkpoint=reference, new_match_inferred=False)
        self.last = report, source
        if self.last_checkpoint is None or elapsed - self.last_checkpoint >= self.checkpoint_seconds:
            self.persist_last("periodic_checkpoint", elapsed)

    def read_error(self, error, elapsed):
        self.continuity_uncertain = True
        self.read_errors += 1
        self.event("read_error", elapsed, reason=str(error)[:300], buffers_absent_inferred=False)

    def finish(self, reason, elapsed):
        if self.finished:
            raise RuntimeStatsError("Recorder already finalized")
        reference = self.persist_last("last_valid_at_stop", elapsed, final=True)
        reason = self.stop_reason or reason
        self.events.append({"kind": "stopped", "elapsed_seconds": elapsed, "reason": reason,
                            "last_valid_checkpoint": reference})
        manifest = {"schema": "pes2021-runtime-watch-experimental-v1", "source_mode": self.source_mode,
                    "created_at": datetime.now(timezone.utc).isoformat(), "stop_reason": reason,
                    "target_native_player_id": self.target_id, "polls": self.polls,
                    "available_observations": self.available_observations,
                    "unavailable_observations": self.unavailable_observations,
                    "read_errors": self.read_errors,
                    "first_unavailable_observation": self.first_unavailable,
                    "last_unavailable_observation": self.last_unavailable,
                    "events": self.events, "snapshots": self.snapshots,
                    "last_valid_checkpoint": reference, "process_instance": self.process_instance,
                    "last_valid_observed_at": self.last[0].get("captured_at") if self.last else None,
                    "identity_review_needed": self.identity_review_needed,
                    "continuity_uncertain": self.continuity_uncertain,
                    "segment_semantics": "observation ordinal only; not an event or career identity",
                    "polling_semantics": "brief buffers can exist and disappear between polls",
                    "live_runtime_acceptance": False, "snapshot_ready": False,
                    "match_ready": False, "d2_ready": False, "event_identity_verified": False,
                    "game_writes": 0, "application_writes": 0,
                    "snapshot_bytes": self.bytes_written}
        payload = encode(manifest)
        if len(payload) > MANIFEST_RESERVE or self.bytes_written + len(payload) > self.max_bytes:
            raise RuntimeStatsError("Manifest exceeds retention budget")
        with (self.output / "manifest.json").open("xb") as stream:
            stream.write(payload)
        self.finished = True
        return manifest


def read_json(path, raw_root=RAW_ROOT, limit=MAX_SAMPLE_BYTES):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(Path(raw_root).resolve()) or path.stat().st_size > limit:
        raise RuntimeStatsError("Replay input outside raw root or oversized")
    payload = path.read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest(), path


def replay(trace_path, recorder, raw_root=RAW_ROOT):
    trace, _, _ = read_json(trace_path, raw_root)
    entries = trace.get("samples", [])
    if not 1 <= len(entries) <= 3600:
        raise RuntimeStatsError("Replay requires 1..3600 ordered samples")
    previous_time = -1
    for entry in entries:
        elapsed = entry["elapsed_seconds"]
        if not isinstance(elapsed, (float, int)) or not previous_time <= elapsed <= 1800:
            raise RuntimeStatsError("Invalid replay ordering/time")
        report, digest, path = read_json(entry["path"], raw_root)
        if digest != entry["sha256"]:
            raise RuntimeStatsError("Replay source SHA256 mismatch")
        recorder.accept(report, elapsed, {"path": str(path), "sha256": digest})
        previous_time = elapsed
        if recorder.stop_reason:
            break
    return recorder.finish("replay_complete", previous_time)


def live(args, recorder):
    started, failures, elapsed = time.monotonic(), 0, 0
    reason = "duration_limit"
    try:
        for _ in range(3600):
            elapsed = time.monotonic() - started
            if elapsed >= args.duration_seconds:
                break
            reader = None
            try:
                reader = WindowsReader(args.pid, args.exe)
                report = capture_stats(reader, args.native_player_id)
                report.update(executable=str(args.exe.resolve()), read_bytes=reader.bytes)
                recorder.accept(report, elapsed)
                failures = 0
            except (RuntimeStatsError, OSError) as exc:
                recorder.read_error(exc, elapsed)
                failures += 1
                if failures >= 3:
                    reason = "consecutive_read_errors"
                    break
            finally:
                if reader:
                    reader.close()
            if recorder.stop_reason:
                break
            time.sleep(min(args.interval_seconds, max(0, args.duration_seconds - (time.monotonic() - started))))
        else:
            reason = "poll_limit"
    except KeyboardInterrupt:
        reason = "interrupted"
    return recorder.finish(reason, time.monotonic() - started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pid", type=int)
    source.add_argument("--replay", type=Path)
    parser.add_argument("--exe", type=Path, default=Path.home() / "Desktop/pes/PES2021.exe")
    parser.add_argument("--native-player-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=300)
    parser.add_argument("--interval-seconds", type=float, default=1)
    parser.add_argument("--checkpoint-seconds", type=float, default=30)
    args = parser.parse_args()
    recorder = None
    try:
        if not 1 <= args.duration_seconds <= 1800 or not 0.5 <= args.interval_seconds <= 30:
            raise RuntimeStatsError("Invalid polling duration/interval")
        if args.pid is not None and not 0 < args.pid < 2**32:
            raise RuntimeStatsError("Invalid PID")
        recorder = Recorder(args.output, "offline_replay" if args.replay else "live_read",
                            args.native_player_id, checkpoint_seconds=args.checkpoint_seconds)
        manifest = replay(args.replay, recorder) if args.replay else live(args, recorder)
        print(json.dumps({"status": "stopped", "output": str(recorder.output),
                          "source_mode": recorder.source_mode, "stop_reason": manifest["stop_reason"],
                          "polls": recorder.polls, "snapshots": len(recorder.snapshots),
                          "last_valid_checkpoint": manifest["last_valid_checkpoint"],
                          "match_ready": False, "d2_ready": False}, ensure_ascii=False))
        return 1 if manifest["stop_reason"] in ("consecutive_read_errors", "process_changed") else 0
    except (RuntimeStatsError, OSError, ValueError, KeyError, TypeError) as exc:
        finalization_error = None
        if recorder is not None and not recorder.finished:
            try:
                recorder.finish("rejected", 0)
            except (RuntimeStatsError, OSError, ValueError, KeyError, TypeError) as finish_exc:
                finalization_error = str(finish_exc)
        print(json.dumps({"status": "rejected", "reason": str(exc),
                          "finalization_error": finalization_error,
                          "game_writes": 0, "application_writes": 0}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
