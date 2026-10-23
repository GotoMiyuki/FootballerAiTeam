"""Read-only PES21 save-container research. Input must be an isolated raw copy.

Container algorithm/key: the4chancup/pesXdecrypter, libpesXcrypter commit
f3e4831a066836690dc3ae48ea95d7217d502c50 (public-domain crypt.c/masterkey.c).
MT initialization derives from Nishimura/Matsumoto; see THIRD_PARTY_NOTICES.txt.
BAL player fields are experimental; match structures remain unverified.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import sys

RAW_ROOT = Path(__file__).resolve().parent / "raw" / "real_test"
MASK = 0xFFFFFFFF
MASTER_KEY = bytes.fromhex(
    "9061d866437724f892bab87121c76063f0919a7ded4780de51f5ddd108fe3284"
    "f5099200b23e889feb244305587600229bfeecf6500029d3427550b9ecd2f675")
# Layout facts from xAranaktu CT 6043cabf2b8b314064d06d63ae5fc3e5f522d2ed.
# No CT assembler, hooks, writes or injected pointer acquisition are executed.
ATTRIBUTE_LAYOUT = {
    "offensive_awareness": (0x03, 0), "ball_control": (0x06, 5),
    "dribbling": (0x05, 6), "tight_possession": (0x08, 0),
    "low_pass": (0x09, 6), "lofted_pass": (0x0A, 5), "finishing": (0x08, 7),
    "heading": (0x0C, 0), "place_kicking": (0x0E, 5), "curl": (0x10, 0),
    "speed": (0x14, 7), "acceleration": (0x18, 7), "kicking_power": (0x18, 0),
    "jump": (0x19, 6), "physical_contact": (0x15, 6), "balance": (0x16, 5),
    "stamina": (0x1A, 5), "defensive_awareness": (0x04, 0),
    "ball_winning": (0x0C, 7), "aggression": (0x0D, 6),
    "gk_awareness": (0x04, 7), "gk_catching": (0x10, 7),
    "gk_clearing": (0x11, 6), "gk_reflexes": (0x12, 5), "gk_reach": (0x14, 0),
}
POSITIONS = ("GK", "CB", "LB", "RB", "DMF", "CMF", "LMF", "RMF", "AMF", "LWF", "RWF", "SS", "CF")


class SaveError(ValueError):
    pass


def mt_by_array(words):
    """MT19937 init_by_array; CPython supplies the matching generator engine."""
    if not words:
        raise SaveError("Empty seed")
    mt = [19650218]
    for i in range(1, 624):
        mt.append((1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & MASK)
    i, j = 1, 0
    for _ in range(max(624, len(words))):
        mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525)) + words[j] + j) & MASK
        i, j = i + 1, j + 1
        if i >= 624:
            mt[0], i = mt[-1], 1
        if j >= len(words):
            j = 0
    for _ in range(623):
        mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941)) - i) & MASK
        i += 1
        if i >= 624:
            mt[0], i = mt[-1], 1
    mt[0] = 0x80000000
    generator = random.Random()
    generator.setstate((3, tuple(mt + [624]), None))
    return generator


def crypt_stream(data: bytes, key: bytes):
    if len(key) != 64:
        raise SaveError("Stream key must be 64 bytes")
    rand = mt_by_array(struct.unpack("<16I", key)).getrandbits
    c0, c1, c2, c3 = (rand(32) for _ in range(4))
    output = bytearray(len(data))
    for offset in range(0, len(data), 4):
        c4 = rand(32)
        length = min(4, len(data) - offset)
        value = int.from_bytes(data[offset:offset + length], "little") ^ c4 ^ c3 ^ c2 ^ c1 ^ c0
        output[offset:offset + length] = (value & ((1 << (8 * length)) - 1)).to_bytes(length, "little")
        c0 = ((c1 >> 15) | (c1 << 17)) & MASK
        c1 = ((c2 << 11) | (c2 >> 21)) & MASK
        c2 = ((c3 << 7) | (c3 >> 25)) & MASK
        c3 = ((c4 >> 13) | (c4 << 19)) & MASK
    return bytes(output)


def xor_param(key, value):
    parameter = struct.pack("<Q", value) * 8
    return bytes(a ^ b for a, b in zip(key, parameter))


def verify_block_hashes(encryption_header, blocks):
    """PES21 encrypted-header digest order, verified on both local BAL copies."""
    if len(encryption_header) != 320:
        raise SaveError("Invalid encryption header size")
    verified = {}
    for index, name in enumerate(("description", "logo", "data", "serial")):
        digest = hashlib.sha512(blocks[name]).digest()
        if digest != encryption_header[index * 64:(index + 1) * 64]:
            raise SaveError(f"Decrypted {name} SHA512 does not match the stored digest")
        verified[name] = True
    return {"algorithm": "SHA512", "blocks_verified": verified}


def decrypt_container(data: bytes):
    """Decrypt upstream FileHeaderNew layout with strict lengths before body reads."""
    if not 528 <= len(data) <= 64_000_000:
        raise SaveError("Truncated or oversized container")
    reversed_key = b"".join(MASTER_KEY[i:i + 8][::-1] for i in range(0, 64, 8))
    header_key = bytes(a ^ b for a, b in zip(data[256:320], reversed_key))
    encryption_header = crypt_stream(data[:320], header_key)[:256] + data[256:320]
    rolling = bytearray(encryption_header[:64])
    for i, value in enumerate(encryption_header[64:]):
        rolling[i & 63] ^= value
    header = crypt_stream(data[320:528], xor_param(rolling, 208))
    data_size, logo_size, desc_size, serial_length = struct.unpack_from("<4I", header, 64)
    sizes = {"description": desc_size, "logo": logo_size, "data": data_size, "serial": serial_length * 2}
    if 528 + sum(sizes.values()) != len(data) or not data_size:
        raise SaveError("Header lengths do not match the file; wrong key/layout or incomplete save")
    blocks, offset = {}, 528
    for index, (name, length) in enumerate(sizes.items()):
        blocks[name] = crypt_stream(data[offset:offset + length], xor_param(rolling, index))
        offset += length
    blocks["header"] = header
    blocks["encryption_header"] = encryption_header
    metadata = {"file_type_hex": header[144:176].hex(), "game_version_hex": header[176:208].hex(),
                "sizes": sizes, "container_layout": "FileHeaderNew", "key_id": "MasterKeyPes21",
                "integrity": verify_block_hashes(encryption_header, blocks)}
    return metadata, blocks


def bits(data, offset, start, length):
    count = (start + length + 7) // 8
    if offset < 0 or offset + count > len(data):
        raise SaveError("Player field outside data block")
    return (int.from_bytes(data[offset:offset + count], "little") >> start) & ((1 << length) - 1)


def parse_bal_snapshot(metadata, blocks):
    """Experimental native snapshot; unknown match layout is not guessed."""
    header = blocks["header"]
    if header[144:176].split(b"\0", 1)[0] != b"BL" or header[176:208].split(b"\0", 1)[0] != b"eFootball PES 2021 SEASON UPDATE":
        raise SaveError("Not the verified PES21 BL container type/version")
    description = blocks["description"]
    if len(description) != 384:
        raise SaveError("Unsupported BAL description layout")
    try:
        slot_title = description[:128].split(b"\0", 1)[0].decode("utf-8")
        lines = description[128:].split(b"\0", 1)[0].decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SaveError("BAL description is not valid UTF-8") from exc
    if len(lines) != 3 or not lines[0] or len(lines[0].encode("utf-8")) > 60 or " / " not in lines[1]:
        raise SaveError("Unsupported BAL identity description")
    name = lines[0]
    club, league = lines[1].split(" / ", 1)
    if not club or not league or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", lines[2]):
        raise SaveError("Missing native club/league/date")
    try:
        saved_date = date(*map(int, lines[2].split("/"))).isoformat()
    except ValueError as exc:
        raise SaveError("Invalid saved game date") from exc
    body = blocks["data"]
    needle, candidates, cursor = name.encode("utf-8") + b"\0", [], 0
    while (hit := body.find(needle, cursor)) != -1:
        cursor = hit + 1
        base = hit - 0x38
        if base < 0 or base + 0x17C > len(body):
            continue
        age, position = bits(body, base + 0x1C, 0, 6), bits(body, base + 7, 4, 4)
        height, weight = body[base], body[base + 1]
        player_id = bits(body, base + 0x30, 0, 32)
        # Candidate guards do not replace version/field validation.
        if 15 <= age <= 60 and position < len(POSITIONS) and 100 <= height <= 230 and 30 <= weight <= 150 and player_id:
            candidates.append((base, player_id, age, position, height, weight))
    if len(candidates) != 1:
        raise SaveError(f"Ambiguous or missing native player record: {len(candidates)} candidates")
    base, player_id, age, position, height, weight = candidates[0]
    attributes = {name: {"value": bits(body, base + offset, start, 7),
                         "data_offset": base + offset, "bit_start": start, "bit_length": 7,
                         "validation": "upstream_layout_candidate_pending_field_comparison"}
                  for name, (offset, start) in ATTRIBUTE_LAYOUT.items()}
    return {"source": "decrypted_bal_save", "status": "experimental_native_snapshot",
            "slot_title": slot_title, "native_player_id": player_id,
            "identity": {"name": name, "club": club, "league": league},
            "saved_game_date_raw": lines[2], "saved_game_date": saved_date, "player_data_offset": base,
            "profile": {"age": age, "registered_position": POSITIONS[position],
                        "registered_position_raw": position, "height_cm": height, "weight_kg": weight},
            "attributes": attributes,
            "unknown_fields": {"overall_rating": "Not located; not inferred from abilities",
                               "match_records": "BAL event/individual-statistics layout not verified",
                               "live_current_state": "Save snapshot may precede unsaved game progress"},
            "application_writes": 0, "game_writes": 0}


def read_copy(path: Path, *, raw_root=RAW_ROOT):
    path, root = Path(path).resolve(), Path(raw_root).resolve()
    if not path.is_relative_to(root):
        raise SaveError("Only an isolated raw/real_test copy may be parsed")
    if not 528 <= path.stat().st_size <= 64_000_000:
        raise SaveError("Truncated or oversized container")
    before = path.read_bytes()
    metadata, blocks = decrypt_container(before)
    if path.read_bytes() != before:
        raise SaveError("Source copy changed during read")
    return {"source_path": str(path), "source_sha256": hashlib.sha256(before).hexdigest(),
            "source_size": len(before), **metadata,
            "blocks": {name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                       for name, value in blocks.items()},
            "bal_fields_verified": False, "application_writes": 0, "game_writes": 0}, blocks


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copy", type=Path)
    parser.add_argument("--output", type=Path, help="New isolated output directory for research blocks")
    parser.add_argument("--bal", action="store_true", help="Experimental native BAL identity/ability snapshot")
    args = parser.parse_args()
    try:
        if args.output is not None:
            output = args.output.resolve()
            if not output.is_relative_to(RAW_ROOT.resolve()) or output.exists():
                raise SaveError("Output must be a new directory inside raw/real_test")
        preview, blocks = read_copy(args.copy)
        if args.bal:
            preview["native_snapshot"] = parse_bal_snapshot(preview, blocks)
        if args.output is not None:
            output.mkdir(parents=True, exist_ok=False)
            for name, value in blocks.items():
                (output / (name + ".bin")).write_bytes(value)
            (output / "container.json").write_text(json.dumps(preview, indent=2), encoding="utf-8")
    except (OSError, SaveError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "experimental_native_snapshot" if args.bal else "container_only", **preview}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
