#!/usr/bin/env python3
"""Dragon Curve — reversible geometric transposition for text, files and folders.

Interactive menu: python3 dragon_curve.py
Text: python3 dragon_curve.py --text "Hello" --key paper [--passes 7]
Text reversal: python3 dragon_curve.py --decrypt --text CIPHERTEXT --key paper
Files: python3 dragon_curve.py --file note.txt --unit character --key paper
Files reversal: python3 dragon_curve.py --decrypt --file note.dragon.txt --key paper
Folders: python3 dragon_curve.py --folder 'My Folder' --cross-weave --compression store

Current self-describing file formats: DRAGON-CURVE-DATA/3,
DRAGON-CURVE-FOLDER/1 (without weaving), DRAGON-CURVE-FOLDER/2 (with weaving).
Legacy DRAGON-CURVE-TEXT/1 and /2 envelopes are no longer supported.
Retain a previous release if you need to decode those older files.

The same key, pass count and transposition unit reverse a transformation.
Text entered directly uses the modern character transposition without a
version envelope; files use DATA/3 headers unless --no-header is selected.
Folder mode ZIP-packages the tree using Deflate or Store, transposes bounded
chunks and can optionally cross-weave data between chunks. It never modifies
an original file or folder, and refuses to overwrite an existing output.

Dragon Curve provides reversible obfuscation, NOT secure encryption.
"""

import base64
import os
from functools import lru_cache
import shutil
import stat
import tempfile
import zipfile
import hashlib
import math
import re
import shlex
import sys
import unicodedata
from pathlib import Path

USAGE = "usage: dragon_curve.py [--menu] [--decrypt] [--key KEY] [--passes 1..7] [--text TEXT | --file FILE | --folder FOLDER] [--unit character|byte|hex|bit] [--output PATH]"

NAME = "dragon_curve.py"

# East, north, west, south. A left turn adds one. A right turn subtracts one.
_DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))
_HEADINGS = ("east", "north", "west", "south")

# FNV-1a 64-bit. The offset basis is also the hash of the empty byte string.
_FNV_OFFSET = 14695981039346656037
_FNV_PRIME = 1099511628211
_MASK64 = 0xFFFFFFFFFFFFFFFF

# A keyed message makes MIN_PASSES to MIN_PASSES + 3 passes.
MIN_PASSES = 4

# Eight ways to read a page. View 0 is the canonical row order.
VIEWS = (
    "rows from the top, left to right",
    "rows from the top, right to left",
    "rows from the bottom, left to right",
    "rows from the bottom, right to left",
    "columns from the left, top to bottom",
    "columns from the left, bottom to top",
    "columns from the right, top to bottom",
    "columns from the right, bottom to top",
)

# One terminal cell per curve segment, plus the gaps between them.
# Past this, the picture is larger than a normal terminal.
MAX_MAP_COLUMNS = 100
MAX_MAP_ROWS = 40
EMPTY_CELL = "·"


class MapLimit(Exception):
    """The curve's bounding box is past the size drawn on a terminal."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(f"{width} by {height}")
        self.width = width
        self.height = height


def paper_turn(n: int) -> int:
    """Return +1 for a left turn after segment n, or -1 for a right turn.

    n starts at 1. The lowest set bit of n is the fold that made this
    crease. The bit above it is 0 for a left turn and 1 for a right turn.
    That is the regular paperfolding sequence, OEIS A014577, with 1 as
    left and 0 as right.
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("turn index must be an integer")
    if n < 1:
        raise ValueError("turn index starts at 1")
    lowest = n & -n
    if n & (lowest << 1):
        return -1
    return 1


def fnv1a_64(data: bytes) -> int:
    """FNV-1a hash of data as an unsigned 64-bit integer."""
    digest = _FNV_OFFSET
    for byte in data:
        digest ^= byte
        digest = (digest * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return digest


def curve_settings(key: str) -> tuple[int, int, int]:
    """Return (fold, heading, skip) for a key.

    fold is +1 for left-first and -1 for right-first. heading is 0 east,
    1 north, 2 west, or 3 south. skip is how many segments of the infinite
    curve come before the message. An empty key is (+1, 0, 0).
    """
    if not isinstance(key, str):
        raise TypeError("key must be a string")
    if key == "":
        return (1, 0, 0)
    digest = fnv1a_64(key.encode("utf-8"))
    fold = -1 if digest & 1 else 1
    heading = (digest >> 1) & 3
    skip = digest >> 3
    return (fold, heading, skip)


def splitmix64(state: int) -> tuple[int, int]:
    """Advance a SplitMix64 state. Return (next state, output word)."""
    state = (state + 0x9E3779B97F4A7C15) & _MASK64
    word = state
    word = ((word ^ (word >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    word = ((word ^ (word >> 27)) * 0x94D049BB133111EB) & _MASK64
    return state, word ^ (word >> 31)




def _describe_pass(fold: int, heading: int, skip: int, view: int) -> str:
    side = "left" if fold == 1 else "right"
    line = f"{side} folds, facing {_HEADINGS[heading]}, segment {skip + 1}"
    if view != 0:
        line += f", read in {VIEWS[view]}"
    return line




def midpoints(
    length: int,
    fold: int = 1,
    heading: int = 0,
    skip: int = 0,
) -> list[tuple[int, int]]:
    """Doubled midpoints of `length` dragon segments, in walk order.

    The midpoint of a segment from (x1, y1) to (x2, y2) is stored as
    (x1 + x2, y1 + y2), so the values stay integers. The paperfolding
    curve does not reuse a segment. A repeated midpoint raises
    RuntimeError, because the reading order would no longer be a
    rearrangement.
    """
    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError("length must be an integer")
    if length < 0:
        raise ValueError("length must be non-negative")
    if fold not in (1, -1):
        raise ValueError("fold must be 1 or -1")
    if heading not in (0, 1, 2, 3):
        raise ValueError("heading must be 0, 1, 2, or 3")
    if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
        raise ValueError("skip must be a non-negative integer")

    x = 0
    y = 0
    points: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for index in range(length):
        step_x, step_y = _DIRS[heading]
        next_x = x + step_x
        next_y = y + step_y
        point = (x + next_x, y + next_y)
        if point in seen:
            raise RuntimeError(
                f"dragon curve repeated a segment at index {index}"
            )
        seen.add(point)
        points.append(point)
        x = next_x
        y = next_y
        heading = (heading + fold * paper_turn(skip + index + 1)) % 4
    return points


def reading_order(points: list[tuple[int, int]], view: int = 0) -> list[int]:
    """Walk indexes in the order a page is read.

    View 0 reads rows from the top, left to right: a higher segment first,
    then the one further left. The other views are listed in VIEWS. Bit 2
    reads columns instead of rows. Bit 1 starts from the other end. Bit 0
    reads each row or column the other way.
    """
    if view not in range(8):
        raise ValueError("view must be 0 to 7")
    outer = -1 if view & 2 else 1
    inner = -1 if view & 1 else 1
    if view & 4:
        def place(i: int) -> tuple[int, int]:
            return (points[i][0] * outer, -points[i][1] * inner)
    else:
        def place(i: int) -> tuple[int, int]:
            return (-points[i][1] * outer, points[i][0] * inner)
    return sorted(range(len(points)), key=place)


def _pass_order(length: int, settings: tuple[int, int, int, int]) -> list[int]:
    fold, heading, skip, view = settings
    return reading_order(midpoints(length, fold, heading, skip), view)
















def map_glyph(char: str) -> str:
    """One terminal column for a character on the curve picture."""
    if char == " ":
        return "␣"
    if char == "\n":
        return "↵"
    if char == "\t":
        return "→"
    if char == "\r":
        return "⏎"
    category = unicodedata.category(char)
    if category.startswith("C") or unicodedata.combining(char):
        return EMPTY_CELL
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return "□"
    return char


def _draw_map(text: str, settings: tuple[int, int, int, int]) -> str:
    """Place the input to one actual pass on that pass's curve."""
    if not text:
        return ""
    fold, heading, skip, _view = settings
    points = midpoints(len(text), fold, heading, skip)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x + 1
    height = max_y - min_y + 1
    if width > MAX_MAP_COLUMNS or height > MAX_MAP_ROWS:
        raise MapLimit(width, height)
    rows = [[EMPTY_CELL] * width for _ in range(height)]
    for char, (point_x, point_y) in zip(text, points):
        rows[max_y - point_y][point_x - min_x] = map_glyph(char)
    return "\n".join("".join(row) for row in rows)




def _pass_states(text: str, plan: list[tuple[int, int, int, int]]):
    """Yield (pass number, settings, input, output) exactly as encoding runs."""
    incoming = text
    for number, settings in enumerate(plan, 1):
        order = _pass_order(len(incoming), settings)
        outgoing = "".join(incoming[index] for index in order)
        yield number, settings, incoming, outgoing
        incoming = outgoing




def _preview(text: str, limit: int = 72) -> str:
    """One-line, escaped, bounded preview of an intermediate message."""
    return repr(text if len(text) <= limit else text[:limit] + "…")




# The filename is NOT cryptographically encrypted. Its original bytes are
# kept in the envelope, shuffled in a separate Dragon Curve domain, and
# verified on recovery. The on-disk name is a short, portable digest.
_FILENAME_MAGIC = b"DCFN1\x00"
_FILENAME_MAX_BYTES = 255


def _validate_filename(name: str) -> str:
    if (not name or name in (".", "..") or "/" in name or "\\" in name
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        raise ValueError("invalid original filename in Dragon Curve metadata")
    if len(name.encode("utf-8")) > _FILENAME_MAX_BYTES:
        raise ValueError("original filename exceeds 255 UTF-8 bytes")
    return name


def _filename_key(key: str) -> str:
    return "Dragon-Curve-Filename/v1:" + key


def scramble_filename(name: str, key: str = "") -> str:
    """Reversible metadata token for a basename, independent of file content."""
    raw = _validate_filename(name).encode("utf-8")
    packet = _FILENAME_MAGIC + raw + hashlib.sha256(raw).digest()[:8]
    shuffled = transform_units(packet, _filename_key(key), "byte", 7)
    return base64.urlsafe_b64encode(shuffled).decode("ascii").rstrip("=")


def restore_filename(token: str, key: str = "") -> str:
    """Recover a validated basename; refuse wrong keys or corrupt metadata."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,1000}", token):
        raise ValueError("invalid scrambled filename token")
    try:
        shuffled = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        packet = transform_units(shuffled, _filename_key(key), "byte", 7, True)
        if not packet.startswith(_FILENAME_MAGIC) or len(packet) < len(_FILENAME_MAGIC) + 9:
            raise ValueError("wrong key or corrupted filename metadata")
        raw, checksum = packet[len(_FILENAME_MAGIC):-8], packet[-8:]
        if hashlib.sha256(raw).digest()[:8] != checksum:
            raise ValueError("wrong key or corrupted filename metadata")
        return _validate_filename(raw.decode("utf-8"))
    except (UnicodeDecodeError, UnicodeError) as exc:
        raise ValueError("wrong key or invalid filename encoding") from exc


def scrambled_path(source: Path, token: str) -> Path:
    """A neutral, short, filesystem-safe output name, with no original extension."""
    short_id = hashlib.sha256(token.encode("ascii")).hexdigest()[:24]
    return source.with_name(f"dc-{short_id}.dragon")


def decoded_path(source: Path, key: str, name_token: str | None) -> Path:
    if name_token is None:
        return sibling_path(source, True)
    return source.with_name(restore_filename(name_token, key))


def sibling_path(path: Path, decrypting: bool) -> Path:
    """Return the file written beside `path` when `--output` is omitted.

    Encrypting `note.txt` writes `note.dragon.txt`. Decrypting
    `note.dragon.txt` writes `note.txt`. Decrypting a name that has no
    `.dragon` marker writes `note.plain.txt`.
    """
    stem = path.stem
    suffix = path.suffix
    if decrypting and stem.endswith(".dragon"):
        return path.with_name(stem[: -len(".dragon")] + suffix)
    if decrypting and suffix == ".dragon":
        return path.with_name(stem)
    if decrypting:
        return path.with_name(stem + ".plain" + suffix)
    return path.with_name(stem + ".dragon" + suffix)
















def _same_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False














class _MenuBack(Exception):
    """Return from a numbered-menu operation without exiting the app."""


class _MenuExit(Exception):
    """Terminal input ended."""


def _ask(prompt: str) -> str:
    try:
        reply = input(prompt)
    except KeyboardInterrupt as exc:
        print("\nBack to menu.")
        raise _MenuBack from exc
    except EOFError as exc:
        raise _MenuExit from exc
    if reply.strip().lower() == ":back":
        raise _MenuBack
    return reply


def _menu_path(raw: str) -> Path:
    """Accept ordinary, quoted, or macOS drag-and-drop escaped file paths."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    path = Path(value).expanduser()
    if not path.exists() and "\\" in value and sys.platform != "win32":
        try:
            tokens = shlex.split(value)
        except ValueError:
            tokens = []
        if len(tokens) == 1:
            path = Path(tokens[0]).expanduser()
    return path


def _ask_key() -> str:
    return _ask("Key phrase (Enter = no key): ")
















# Current file format: DATA/3; separate from both active folder formats.
DATA_PREFIX = b"DRAGON-CURVE-DATA/3 "
UNITS = ("character", "hex", "byte", "bit")


def _selected_plan(key: str, passes: int | None):
    if passes is None:
        return pass_plan(key)
    if not 1 <= passes <= 7:
        raise ValueError("passes must be 1 through 7")
    if not key:
        return [(1, 0, 0, 0)] * passes
    state, unused = splitmix64(fnv1a_64(key.encode("utf-8")))
    result = []
    for unused in range(passes):
        state, word = splitmix64(state)
        result.append((-1 if word & 1 else 1, (word >> 1) & 3,
                       word >> 6, (word >> 3) & 7))
    return result


def transform_units(data: bytes, key: str = "", unit: str = "byte",
                    passes: int | None = None, reverse: bool = False) -> bytes:
    """Exact invertible transposition of Unicode characters, nibbles, bytes or bits.

    The nibble/bit numbering is most-significant-first in each byte.
    Character mode decodes and re-encodes strict UTF-8, making it unsuitable
    after a stage that produces invalid UTF-8.
    """
    if unit not in UNITS:
        raise ValueError("unit must be character, hex, byte, or bit")
    if unit == "character":
        values = list(data.decode("utf-8"))
    elif unit == "byte":
        values = list(data)
    elif unit == "hex":
        values = [n for b in data for n in (b >> 4, b & 15)]
    else:
        values = [(b >> shift) & 1 for b in data for shift in range(7, -1, -1)]
    plan = _selected_plan(key, passes)
    for settings in (reversed(plan) if reverse else plan):
        order = _pass_order(len(values), settings)
        if reverse:
            restored = [0] * len(values)
            for rank, position in enumerate(order):
                restored[position] = values[rank]
            values = restored
        else:
            values = [values[position] for position in order]
    if unit == "character":
        return "".join(values).encode("utf-8")
    if unit == "byte":
        return bytes(values)
    if unit == "hex":
        return bytes((values[i] << 4) | values[i + 1] for i in range(0, len(values), 2))
    return bytes(sum(values[i + bit] << (7 - bit) for bit in range(8))
                 for i in range(0, len(values), 8))


def _data_header_info(data: bytes) -> tuple[str, int, bool, str | None, bytes] | None:
    """Return (unit, passes, keyed, optional filename token, ciphertext)."""
    if data.startswith(b"DRAGON-CURVE-DATA/") and not data.startswith(DATA_PREFIX):
        raise ValueError("unsupported DATA format (only DATA/3 is supported)")
    if not data.startswith(DATA_PREFIX):
        return None
    line, sep, payload = data.partition(b"\n")
    if not sep:
        raise ValueError("incomplete binary header")
    match = re.fullmatch(
        rb"DRAGON-CURVE-DATA/3 mode=(character|hex|byte|bit) passes=([1-7]) key=([01])(?: name=([A-Za-z0-9_-]{1,1000}))?",
        line,
    )
    if not match:
        raise ValueError("invalid binary Dragon Curve header")
    return (match.group(1).decode("ascii"), int(match.group(2)),
            match.group(3) == b"1",
            match.group(4).decode("ascii") if match.group(4) else None,
            payload)


def encode_data(data: bytes, key: str = "", unit: str = "byte",
                passes: int | None = None, header: bool = True,
                name_token: str | None = None) -> bytes:
    if name_token is not None and not header:
        raise ValueError("scrambled filenames require a version header")
    result = transform_units(data, key, unit, passes)
    if not header:
        return result
    count = len(_selected_plan(key, passes))
    metadata = f" name={name_token}" if name_token is not None else ""
    line = f"DRAGON-CURVE-DATA/3 mode={unit} passes={count} key={int(bool(key))}{metadata}\n"
    return line.encode("ascii") + result


def decode_data(data: bytes, key: str = "", unit: str = "byte",
                passes: int | None = None) -> bytes:
    if data.startswith(b"DRAGON-CURVE-TEXT/"):
        raise ValueError("TEXT/1 and TEXT/2 are retired; use an earlier Dragon Curve release to decode them")
    info = _data_header_info(data)
    if info is not None:
        unit, passes, keyed, _token, data = info
        if keyed != bool(key):
            raise ValueError("key presence does not match file header")
    return transform_units(data, key, unit, passes, reverse=True)


def _menu_unit() -> str:
    print("  1. Character (UTF-8 text)")
    print("  2. Hex digit (4-bit nibble)")
    print("  3. Byte (8-bit, arbitrary file data)")
    print("  4. Bit (individual binary digits)")
    choice = _ask("Unit [3]: ").strip() or "3"
    if choice not in ("1", "2", "3", "4"):
        raise ValueError("choose unit 1–4")
    return ("character", "hex", "byte", "bit")[int(choice)-1]


def _menu_binary_file(decrypting: bool) -> None:
    src = _menu_path(_ask("Input file (drag and drop works): "))
    data = src.read_bytes()
    if decrypting and data.startswith(b"DRAGON-CURVE-TEXT/"):
        raise ValueError("TEXT/1 and TEXT/2 are retired; use an earlier Dragon Curve release")
    if decrypting and data.startswith(DATA_PREFIX):
        print("Recognized Dragon Curve v3 header; unit and passes detected automatically.")
        unit, passes = "byte", None
    else:
        unit = _menu_unit()
        raw = _ask("Passes 1–7 [Enter = automatic]: ").strip()
        passes = int(raw) if raw else None
    key = _ask_key()
    if decrypting:
        result = decode_data(data, key, unit, passes)
        info = _data_header_info(data)
        token = info[3] if info is not None else None
        if token is not None:
            restore_filename(token, key)
            print("Scrambled filename metadata detected; original name will be restored.")
    else:
        scramble = _ask("Scramble output filename? [y/N]: ").strip().lower() in {"y", "yes"}
        token = scramble_filename(src.name, key) if scramble else None
        result = encode_data(data, key, unit, passes, header=True, name_token=token)
    default = (decoded_path(src, key, token) if decrypting else
               scrambled_path(src, token) if token is not None else
               sibling_path(src, False))
    reply = _ask(f"Output file [Enter = {default}]: ").strip()
    dest = _menu_path(reply) if reply else default
    if _same_path(src, dest) or dest.exists() or dest.is_symlink():
        raise OSError("destination exists or equals input; choose a new filename")
    dest.write_bytes(result)
    print(f"Wrote {dest} ({len(result)} bytes)\n")


def _binary_cli(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Dragon Curve v3: binary-capable transposition")
    parser.add_argument("--unit", choices=UNITS, default=None,
                        help="unit for encryption (default byte); decryption auto-detects DATA/3")
    parser.add_argument("--passes", type=int, default=None)
    parser.add_argument("--key", default="")
    parser.add_argument("--decrypt", action="store_true")
    parser.add_argument("--no-header", action="store_true")
    parser.add_argument("--scramble-name", action="store_true",
                        help="hide original basename and extension (automatically restored on decrypt)")
    parser.add_argument("--map", action="store_true", help="trace character-unit passes to stderr")
    parser.add_argument("--map-pass", type=int, help="show just one numbered pass (requires --map)")
    parser.add_argument("--file", required=True)
    parser.add_argument("--output")
    opts = parser.parse_args(argv)
    if opts.passes is not None and not 1 <= opts.passes <= 7:
        parser.error("--passes must be 1–7")
    if opts.map_pass is not None and not opts.map:
        parser.error("--map-pass requires --map")
    if opts.scramble_name and opts.decrypt:
        parser.error("--scramble-name is only for encryption; restoration is automatic")
    if opts.scramble_name and opts.no_header:
        parser.error("--scramble-name requires the version header; omit --no-header")
    src = Path(opts.file).expanduser()
    try:
        data = src.read_bytes()
        selected_unit = opts.unit or "byte"
        if opts.decrypt:
            result = decode_data(data, opts.key, selected_unit, opts.passes)
            info = _data_header_info(data)
            token = info[3] if info is not None else None
            if token is not None:
                restore_filename(token, opts.key)  # validate even with --output
        else:
            token = scramble_filename(src.name, opts.key) if opts.scramble_name else None
            result = encode_data(data, opts.key, selected_unit, opts.passes,
                                 not opts.no_header, name_token=token)
        dest = Path(opts.output).expanduser() if opts.output else (
            decoded_path(src, opts.key, token) if opts.decrypt else
            scrambled_path(src, token) if token is not None else
            sibling_path(src, False))
        if _same_path(src, dest) or dest.exists() or dest.is_symlink():
            parser.error("destination already exists or equals input; choose a different --output")
        if opts.map:
            map_unit, map_passes = selected_unit, opts.passes
            if opts.decrypt and data.startswith(DATA_PREFIX):
                import re
                line = data.partition(b"\n")[0]
                match = re.fullmatch(
                    rb"DRAGON-CURVE-DATA/3 mode=(character|hex|byte|bit) passes=([1-7]) key=[01](?: name=[A-Za-z0-9_-]+)?", line)
                if match:
                    map_unit = match.group(1).decode("ascii")
                    map_passes = int(match.group(2))
            if map_unit != "character":
                parser.error("--map requires character unit (not binary/hex/bit)")
            actual_count = len(_selected_plan(opts.key, map_passes))
            if opts.map_pass is not None and not 1 <= opts.map_pass <= actual_count:
                parser.error(f"--map-pass must be 1–{actual_count}")
            plain = result.decode("utf-8") if opts.decrypt else data.decode("utf-8")
            _emit_map(plain, opts.key, map_passes, opts.map_pass)
        dest.write_bytes(result)
        print(f"Wrote {dest} ({len(result)} bytes)")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


# Folder/1 is the current non-weave format; Folder/2 enables cross-chunk weaving.
# A normal ZIP is created first, then reversible Dragon Curve permutations
# are applied to bounded chunks of the ZIP bytes. This is NOT encryption.
FOLDER_PREFIX = b"DRAGON-CURVE-FOLDER/1 "
FOLDER_WEAVE_PREFIX = b"DRAGON-CURVE-FOLDER/2 "
_FOLDER_WEAVE_LANES = 16  # Sixteen spatially distributed stripes per chunk.
FOLDER_MODES = ("byte", "hex", "bit")
_FOLDER_MAX_UNITS = 16384  # Keep memory usage bounded even for large archives.
_FOLDER_CHUNK_BYTES = {"byte": 16384, "hex": 8192, "bit": 2048}
# ZIP records the compression method for each member. No format/header change
# is needed: even older folder decoders can restore Store packages.
FOLDER_COMPRESSIONS = {"deflate": zipfile.ZIP_DEFLATED,
                       "store": zipfile.ZIP_STORED}


@lru_cache(maxsize=20)
def _folder_permutation(length: int, key: str, passes: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Compose all dragon passes once and reuse them for every equally sized chunk.

    This preserves a real geometric transposition without recalculating the
    expensive curve geometry for every block of a large folder archive.
    """
    plan = _selected_plan("Dragon-Curve-Folder/v1:" + key, passes)
    forward = list(range(length))
    for setting in plan:
        ordering = _pass_order(length, setting)
        forward = [forward[i] for i in ordering]
    inverse = [0] * length
    for ciphertext_index, original_index in enumerate(forward):
        inverse[original_index] = ciphertext_index
    return tuple(forward), tuple(inverse)


def _folder_transform(block: bytes, key: str, index: int, unit: str,
                      passes: int, reverse: bool = False) -> bytes:
    """Rearrange one bounded chunk; a different cyclic offset per chunk.

    The offset is also a transposition, not a substitution. It makes repeated
    identical input chunks yield different arrangements. The keyed geometric
    permutation is computed once per chunk length and reused.
    """
    if unit == "byte":
        values = list(block)
    elif unit == "hex":
        values = [digit for b in block for digit in (b >> 4, b & 15)]
    else:
        values = [(b >> shift) & 1 for b in block for shift in range(7, -1, -1)]
    n = len(values)
    if not n:
        return b""
    forward, inverse = _folder_permutation(n, key, passes)
    offset = (index * 7919) % n
    if reverse:
        # Invert the final cyclic shift, then invert the curve permutation.
        shifted = values[-offset:] + values[:-offset] if offset else values
        values = [shifted[j] for j in inverse]
    else:
        values = [values[j] for j in forward]
        if offset:
            values = values[offset:] + values[:offset]
    if unit == "byte":
        return bytes(values)
    if unit == "hex":
        return bytes((values[j] << 4) | values[j+1] for j in range(0, n, 2))
    return bytes(sum(values[i + bit] << (7 - bit) for bit in range(8))
                 for i in range(0, n, 8))


def _folder_weave_offsets(chunks: int, key: str, passes: int) -> tuple[int, ...]:
    """Choose a deterministic Dragon-curve keyed displacement for each stripe.

    For each output chunk, its 16 stripes come from distributed input chunks.
    A single window never needs to hold the entire archive in memory. No
    independent chunk can be recovered without the surrounding weave.
    """
    if chunks < 1:
        raise ValueError("invalid folder archive length")
    # Approximately one sixteenth of the archive between successive sources.
    # Coprime step spreads lanes even when chunk count is a round number.
    step = max(1, chunks // _FOLDER_WEAVE_LANES)
    while math.gcd(step, chunks) != 1:
        step += 1
    digest = fnv1a_64(("Dragon-Curve-Folder/v2/weave:" + key).encode("utf-8"))
    base = digest % chunks
    # Spatial, not alphabetic, lane ordering: a small actual dragon curve.
    order, _ = _folder_permutation(_FOLDER_WEAVE_LANES, key + "/weave", passes)
    return tuple((base + step * lane) % chunks for lane in order)


def _folder_weave_file(source: Path, destination: Path, key: str,
                       passes: int, chunk_bytes: int, original_bytes: int,
                       reverse: bool = False) -> None:
    """Disk-backed, reversible cross-chunk stripe distribution.

    Forward: input chunk i, lane j moves to output chunk
    (i + offsets[j]) % chunk_count, preserving lane j's byte positions.
    Reverse applies the inverse displacement. The final partial ZIP chunk
    is zero-padded only inside the woven package and trimmed after reversal.
    The disk-backed scatter avoids storing a large folder in RAM.
    """
    if chunk_bytes <= 0 or chunk_bytes % _FOLDER_WEAVE_LANES:
        raise ValueError("invalid weave chunk size")
    if original_bytes <= 0:
        raise ValueError("invalid folder archive length")
    chunks = (original_bytes + chunk_bytes - 1) // chunk_bytes
    padded = chunks * chunk_bytes
    stripe_bytes = chunk_bytes // _FOLDER_WEAVE_LANES
    offsets = _folder_weave_offsets(chunks, key, passes)
    expected_input_bytes = padded if reverse else original_bytes
    if source.stat().st_size != expected_input_bytes:
        raise ValueError("folder weave input length does not match header")
    with source.open("rb") as incoming, destination.open("w+b") as outgoing:
        outgoing.truncate(padded)
        for source_row in range(chunks):
            block = incoming.read(chunk_bytes)
            if len(block) != chunk_bytes:
                if reverse or source_row != chunks - 1:
                    raise ValueError("incomplete folder weave data")
                block = block.ljust(chunk_bytes, b"\0")
            for lane, displacement in enumerate(offsets):
                # The same reversible lane-wise map is used for all rows.
                target_row = ((source_row - displacement) if reverse
                              else (source_row + displacement)) % chunks
                start = lane * stripe_bytes
                outgoing.seek(target_row * chunk_bytes + start)
                outgoing.write(block[start:start + stripe_bytes])
        if reverse:
            # The last chunk's zeros must be present after restoring positions.
            outgoing.seek(original_bytes)
            if any(outgoing.read(padded - original_bytes)):
                raise ValueError("folder weave padding damaged or wrong key")
            outgoing.truncate(original_bytes)


def _folder_header(data: bytes) -> tuple[str, int, bool, int, int, str, str | None, bool]:
    """Parse Folder/1 (standard) or Folder/2 (woven) package headers."""
    line = data.rstrip(b"\r\n")
    match = re.fullmatch(
        rb"DRAGON-CURVE-FOLDER/([12]) mode=(byte|hex|bit) passes=([1-7]) key=([01]) "
        rb"chunk=([0-9]{1,5}) bytes=([0-9]{1,20}) sha256=([0-9a-f]{64})"
        rb"( weave=16)?(?: name=([A-Za-z0-9_-]{1,1000}))?", line,
    )
    if not match:
        raise ValueError("invalid or unsupported Dragon Curve folder header")
    version = int(match.group(1))
    woven = bool(match.group(8))
    if woven != (version == 2):
        raise ValueError("unsupported folder weave format")
    mode = match.group(2).decode("ascii")
    size = int(match.group(5))
    if size != _FOLDER_CHUNK_BYTES[mode]:
        raise ValueError("unsupported folder chunk size")
    total_bytes = int(match.group(6))
    if total_bytes <= 0:
        raise ValueError("invalid folder archive length")
    return (mode, int(match.group(3)), match.group(4) == b"1", size,
            total_bytes, match.group(7).decode("ascii"),
            match.group(9).decode("ascii") if match.group(9) else None, woven)


def _folder_members(folder: Path) -> list[tuple[Path, str, bool]]:
    """Safely collect files/dirs, including the root and empty directories."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("input folder must be a real directory, not a symlink")
    root_name = _validate_filename(folder.name)
    entries = []

    def visit(current: Path, relative: str) -> None:
        entries.append((current, relative + "/", True))
        for child in sorted(current.iterdir(), key=lambda p: p.name):
            _validate_filename(child.name)
            if child.is_symlink():
                raise ValueError(f"symbolic links are not supported: {child}")
            member = relative + "/" + child.name
            if child.is_dir():
                visit(child, member)
            elif child.is_file():
                entries.append((child, member, False))
            else:
                raise ValueError(f"unsupported special file: {child}")

    visit(folder, root_name)
    return entries


def _build_folder_zip(folder: Path, destination: Path,
                      compression: str = "deflate") -> tuple[int, int]:
    if compression not in FOLDER_COMPRESSIONS:
        raise ValueError("folder compression must be 'deflate' or 'store'")
    members = _folder_members(folder)  # Validate before beginning to package.
    file_count = 0
    dir_count = 0
    with zipfile.ZipFile(destination, "w", compression=FOLDER_COMPRESSIONS[compression],
                         allowZip64=True) as archive:
        for path, member, is_dir in members:
            if is_dir:
                archive.write(path, arcname=member)
                dir_count += 1
            else:
                archive.write(path, arcname=member)
                file_count += 1
    return file_count, dir_count


def _check_destination_free(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise OSError(f"destination already exists: {destination}")


def _commit_exclusive_file(staged: Path, destination: Path) -> None:
    """Publish a completed file without replacing any existing destination."""
    _check_destination_free(destination)
    try:
        os.link(staged, destination)  # Atomic create-if-absent on usual filesystems.
    except OSError:
        # Some filesystems disallow hard links. Use exclusive creation instead.
        with destination.open("xb") as out:
            try:
                with staged.open("rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise


def _folder_default_encoded(source: Path, key: str, scramble: bool) -> Path:
    if scramble:
        return scrambled_path(source, scramble_filename(source.name, key))
    return source.with_name(source.name + ".dragon")


def encode_folder(source: Path, output: Path | None = None, key: str = "",
                  unit: str = "byte", passes: int | None = None,
                  scramble_name: bool = False,
                  compression: str = "deflate",
                  cross_weave: bool = False) -> Path:
    """Encode a complete folder into one chunked Dragon Curve package.

    No source file is touched. Temporary files are removed on failure.
    """
    source = Path(source).expanduser()
    if unit not in FOLDER_MODES:
        raise ValueError("folder mode must be byte, hex or bit (not character)")
    if compression not in FOLDER_COMPRESSIONS:
        raise ValueError("folder compression must be 'deflate' or 'store'")
    count = len(_selected_plan(key, passes))
    _validate_filename(source.name)
    destination = (Path(output).expanduser() if output is not None else
                   _folder_default_encoded(source, key, scramble_name))
    if source.is_symlink() or not source.is_dir():
        raise OSError(f"input is not a regular folder: {source}")
    if source.resolve() in destination.resolve().parents:
        raise ValueError("the encoded output must be outside the input folder")
    _check_destination_free(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    chunk_bytes = _FOLDER_CHUNK_BYTES[unit]
    token = scramble_filename(source.name, key) if scramble_name else None
    with tempfile.TemporaryDirectory(prefix=".dragon-folder-", dir=destination.parent) as temp:
        archive_path = Path(temp) / "source.zip"
        staged = Path(temp) / "package.tmp"
        _build_folder_zip(source, archive_path, compression=compression)
        digest = hashlib.sha256()
        with archive_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        total_size = archive_path.stat().st_size
        metadata = f" name={token}" if token is not None else ""
        version = 2 if cross_weave else 1
        weave_field = f" weave={_FOLDER_WEAVE_LANES}" if cross_weave else ""
        header = (f"DRAGON-CURVE-FOLDER/{version} mode={unit} passes={count} "
                  f"key={int(bool(key))} chunk={chunk_bytes} bytes={total_size} "
                  f"sha256={digest.hexdigest()}{weave_field}{metadata}\n").encode("ascii")
        # Preserve Folder/1 output exactly when weaving is disabled.
        transform_input = archive_path
        if cross_weave:
            transform_input = Path(temp) / "woven.tmp"
            _folder_weave_file(archive_path, transform_input, key, count,
                               chunk_bytes, total_size)
        with transform_input.open("rb") as src, staged.open("wb") as dst:
            dst.write(header)
            for index, block in enumerate(iter(lambda: src.read(chunk_bytes), b"")):
                dst.write(_folder_transform(block, key, index, unit, count))
        _commit_exclusive_file(staged, destination)
    return destination


def _unpack_folder(source: Path, key: str, archive_path: Path) -> str | None:
    """Reverse standard or woven stream; verify bytes before extraction."""
    with source.open("rb") as encrypted:
        header = encrypted.readline(2048)
        if not header.endswith(b"\n"):
            raise ValueError("missing, oversized, or incomplete folder header")
        (mode, count, keyed, chunk_bytes, expected_size,
         checksum, name_token, woven) = _folder_header(header)
        if keyed != bool(key):
            raise ValueError("key presence does not match folder header")
        expected_stream_bytes = ((expected_size + chunk_bytes - 1) // chunk_bytes
                                 * chunk_bytes if woven else expected_size)
        # The last partial block is padded only in woven v2 packages.
        with tempfile.TemporaryDirectory(prefix=".dragon-unweave-",
                                         dir=archive_path.parent) as temp:
            transformed_path = Path(temp) / "chunks.tmp" if woven else archive_path
            total = 0
            with transformed_path.open("wb") as plain:
                for index, block in enumerate(iter(lambda: encrypted.read(chunk_bytes), b"")):
                    piece = _folder_transform(block, key, index, mode, count, reverse=True)
                    plain.write(piece)
                    total += len(piece)
                    if total > expected_stream_bytes:
                        raise ValueError("folder payload is longer than the header declares")
            if total != expected_stream_bytes:
                raise ValueError("folder payload length does not match header")
            if woven:
                _folder_weave_file(transformed_path, archive_path, key, count,
                                   chunk_bytes, expected_size, reverse=True)
            digest = hashlib.sha256()
            with archive_path.open("rb") as result:
                for piece in iter(lambda: result.read(1024 * 1024), b"" ):
                    digest.update(piece)
            if digest.hexdigest() != checksum:
                raise ValueError("wrong key or damaged folder data (checksum mismatch)")
    return restore_filename(name_token, key) if name_token is not None else None


def _safe_folder_zip_members(archive: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """Only permit a single-root directory hierarchy with safe regular files."""
    members = archive.infolist()
    if not members:
        raise ValueError("folder archive is empty")
    roots: set[str] = set()
    found: set[str] = set()
    file_entries: set[str] = set()
    folder_entries: set[str] = set()
    for info in members:
        raw = info.filename
        if not raw or raw.startswith("/") or "\\" in raw or "\x00" in raw:
            raise ValueError("unsafe path in folder archive")
        components = raw.rstrip("/").split("/")
        if len(components) < 1 or any(not component for component in components):
            raise ValueError("invalid empty directory component in archive")
        for component in components:
            _validate_filename(component)
        name = "/".join(components)
        if name in found:
            raise ValueError("duplicate path in folder archive")
        found.add(name)
        roots.add(components[0])
        if len(roots) > 1:
            raise ValueError("folder archive contains multiple root directories")
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            raise ValueError("archive contains a symbolic link")
        if info.is_dir():
            if stat.S_IFMT(mode) not in (0, stat.S_IFDIR):
                raise ValueError("invalid directory entry type")
            folder_entries.add(name)
        else:
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError("unsupported archive member type")
            file_entries.add(name)
    root = next(iter(roots))
    if root not in folder_entries:
        raise ValueError("folder archive must contain a root directory")
    for name in found:
        parts = name.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in file_entries:
                raise ValueError("file used as parent directory in archive")
    return root, members


def _restore_folder_zip(archive: zipfile.ZipFile, entries: list[zipfile.ZipInfo],
                        stage: Path) -> None:
    stage.mkdir()  # Isolated extraction folder, not the user's target.
    for info in entries:
        pieces = info.filename.rstrip("/").split("/")
        relative = pieces[1:]  # Strip the single safe archive root directory.
        if not relative:
            continue
        target = stage.joinpath(*relative)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, target.open("xb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)  # ZIP CRC checked on EOF.


def decode_folder(source: Path, output: Path | None = None, key: str = "") -> Path:
    """Restore a folder. Do not expose partial data or overwrite an existing tree."""
    source = Path(source).expanduser()
    if not source.is_file() or source.is_symlink():
        raise OSError(f"input is not a regular file: {source}")
    # Staging directory is placed beside the encoded file or the target root.
    parent = Path(output).expanduser().parent if output is not None else source.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dragon-folder-", dir=parent) as temp:
        archive_path = Path(temp) / "restored.zip"
        name_from_token = _unpack_folder(source, key, archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            root, members = _safe_folder_zip_members(archive)
            if name_from_token is not None and root != name_from_token:
                raise ValueError("scrambled folder name does not match archive root")
            destination = Path(output).expanduser() if output is not None else source.parent / root
            _check_destination_free(destination)
            staged = Path(temp) / "restored_folder"
            _restore_folder_zip(archive, members, staged)
        _check_destination_free(destination)
        # On POSIX, rename is atomic; on Windows, a directory cannot be
        # replaced by another directory with contents. Checked twice above.
        staged.rename(destination)
    return destination


def _menu_folder(decrypting: bool) -> None:
    label = "Encoded folder file" if decrypting else "Input folder"
    src = _menu_path(_ask(f"{label} (drag and drop works): "))
    if decrypting:
        key = _ask_key()
        custom = _ask("Restore into folder path [Enter = original name beside input]: ").strip()
        output = _menu_path(custom) if custom else None
        result = decode_folder(src, output=output, key=key)
        print(f"Restored folder: {result}\n")
    else:
        print("The folder is first packed as a ZIP archive, then transposed in chunks.")
        print("ZIP archive method:")
        print("  1. Deflate (compress files; current default)")
        print("  2. Store (no compression; for comparing raw data)")
        zip_choice = _ask("ZIP archive method [1]: ").strip() or "1"
        if zip_choice not in ("1", "2"):
            raise ValueError("choose ZIP method 1 or 2")
        compression = {"1": "deflate", "2": "store"}[zip_choice]
        print("  1. Byte (recommended, works for all files)")
        print("  2. Hex (slower)")
        print("  3. Bit (slowest)")
        choice = _ask("Folder transposition unit [1]: ").strip() or "1"
        if choice not in ("1", "2", "3"):
            raise ValueError("choose folder mode 1–3")
        unit = {"1":"byte", "2":"hex", "3":"bit"}[choice]
        raw = _ask("Passes 1–7 [Enter = automatic]: ").strip()
        passes = int(raw) if raw else None
        key = _ask_key()
        cross_weave = _ask("Cross-chunk weave (mix distant folder regions)? [y/N]: ").strip().lower() in ("y", "yes")
        scramble = _ask("Scramble output filename? [y/N]: ").strip().lower() in ("y", "yes")
        default = _folder_default_encoded(src, key, scramble)
        custom = _ask(f"Output package [Enter = {default}]: ").strip()
        output = _menu_path(custom) if custom else default
        result = encode_folder(src, output=output, key=key, unit=unit,
                               passes=passes, scramble_name=scramble,
                               compression=compression,
                               cross_weave=cross_weave)
        weave_note = ", cross-chunk weave" if cross_weave else ""
        print(f"Saved folder package ({compression.title()} ZIP{weave_note}): {result}\n")


def _folder_cli(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Dragon Curve folder packaging (reversible obfuscation)")
    parser.add_argument("--folder", required=True, help="input folder when encoding, .dragon file when decoding")
    parser.add_argument("--decrypt", action="store_true")
    parser.add_argument("--key", default="")
    parser.add_argument("--unit", choices=FOLDER_MODES, default=None,
                        help="byte (default), hex, or bit; encryption only")
    parser.add_argument("--compression", choices=tuple(FOLDER_COMPRESSIONS),
                        default=None, help="deflate (default) or store (no compression); encryption only")
    parser.add_argument("--cross-weave", action="store_true",
                        help="optionally spread stripes between distant folder chunks; encryption only")
    parser.add_argument("--passes", type=int, default=None, help="1 to 7; encryption only")
    parser.add_argument("--scramble-name", action="store_true", help="hide encoded output basename")
    parser.add_argument("--output", help="encoded file when encrypting; restored folder path when decrypting")
    opts = parser.parse_args(argv)
    if opts.decrypt and (opts.unit is not None or opts.passes is not None or
                         opts.scramble_name or opts.compression is not None or opts.cross_weave):
        parser.error("--unit, --passes, --compression, --cross-weave and --scramble-name apply only when encoding a folder")
    try:
        if opts.decrypt:
            path = decode_folder(opts.folder, output=opts.output, key=opts.key)
            print(f"Restored folder: {path}")
        else:
            path = encode_folder(opts.folder, output=opts.output, key=opts.key,
                                 unit=opts.unit or "byte", passes=opts.passes,
                                 scramble_name=opts.scramble_name,
                                 compression=opts.compression or "deflate",
                                 cross_weave=opts.cross_weave)
            print(f"Wrote folder package: {path} ({path.stat().st_size:,} bytes)")
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0







# Modern text helpers share the DATA/3 Character mode's exact permutation.
# They intentionally produce no TEXT/1 or TEXT/2 envelopes.
def pass_plan(key: str) -> list[tuple[int,int,int,int]]:
    """Automatic modern plan: one pass unkeyed, 4–7 keyed."""
    if not isinstance(key, str):
        raise TypeError("key must be a string")
    if not key:
        return [(1, 0, 0, 0)]
    state, first = splitmix64(fnv1a_64(key.encode("utf-8")))
    count = MIN_PASSES + (first >> 62)
    plan = []
    for _ in range(count):
        state, word = splitmix64(state)
        plan.append((-1 if word & 1 else 1, (word >> 1) & 3,
                     word >> 6, (word >> 3) & 7))
    return plan


def describe_curve(key: str) -> str:
    plan = pass_plan(key)
    if len(plan) == 1:
        return _describe_pass(*plan[0])
    return "\n".join([f"{len(plan)} passes"] +
                     [f"pass {i}: {_describe_pass(*p)}" for i,p in enumerate(plan, 1)])


def encrypt(text: str, key: str = "", passes: int | None = None) -> str:
    """Modern raw character transposition; no versioned text header."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return transform_units(text.encode("utf-8"), key, "character", passes).decode("utf-8")


def decrypt(text: str, key: str = "", passes: int | None = None) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if text.startswith("DRAGON-CURVE-TEXT/"):
        raise ValueError("TEXT/1 and TEXT/2 are retired; decode with an earlier Dragon Curve release")
    return transform_units(text.encode("utf-8"), key, "character", passes, reverse=True).decode("utf-8")


def _map_plan(key: str, passes: int | None):
    return _selected_plan(key, passes)


def curve_map(text: str, key: str = "", pass_number: int = 1,
              passes: int | None = None) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    plan = _selected_plan(key, passes)
    if not 1 <= pass_number <= len(plan):
        raise ValueError(f"map pass must be 1 through {len(plan)}")
    for number, settings, incoming, _out in _pass_states(text, plan):
        if number == pass_number:
            return _draw_map(incoming, settings)
    return ""


def trace_map(text: str, key: str = "", passes: int | None = None,
              pass_number: int | None = None) -> str:
    """Diagnose modern character-mode passes, without changing the data."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    plan = _selected_plan(key, passes)
    if pass_number is not None and not 1 <= pass_number <= len(plan):
        raise ValueError(f"map pass must be 1 through {len(plan)}")
    lines = ["DRAGON CURVE MAP — DATA/3 character mode",
             f"{len(plan)} pass(es), {len(text)} character(s)"]
    for number, settings, incoming, outgoing in _pass_states(text, plan):
        if pass_number is not None and pass_number != number:
            continue
        lines.extend(("", f"PASS {number}/{len(plan)} — {_describe_pass(*settings)}",
                      f"Read order: {VIEWS[settings[3]]}",
                      f"Input  ({len(incoming)}): {_preview(incoming)}"))
        try:
            lines.append(_draw_map(incoming, settings) or "(Empty curve)")
        except MapLimit as exc:
            lines.append(f"(Map too large: {exc.width} by {exc.height}.)")
        lines.append(f"Output ({len(outgoing)}): {_preview(outgoing)}")
    return "\n".join(lines)


def _emit_map(text: str, key: str, passes: int | None = None,
              pass_number: int | None = None) -> None:
    print(trace_map(text, key, passes, pass_number), file=sys.stderr)


def _menu_passes() -> int | None:
    raw = _ask("Passes 1–7 [Enter = automatic]: ").strip()
    if not raw:
        return None
    try:
        count = int(raw)
    except ValueError as exc:
        raise ValueError("choose 1–7 or Enter") from exc
    if not 1 <= count <= 7:
        raise ValueError("choose 1–7 or Enter")
    return count


def _menu_text(decrypting: bool) -> None:
    prompt = "Ciphertext (one line): " if decrypting else "Plaintext (one line): "
    data = _ask(prompt)
    key = _ask_key()
    passes = _menu_passes()
    result = decrypt(data, key, passes) if decrypting else encrypt(data, key, passes)
    print("\nResult:\n" + result + "\n")


def _menu_map() -> None:
    text = _ask("Plaintext to show on curve (one line): ")
    key = _ask_key()
    passes = _menu_passes()
    count = len(_selected_plan(key, passes))
    pick = _ask(f"View pass [1–{count}, Enter = all]: ").strip()
    try:
        number = int(pick) if pick else None
    except ValueError as exc:
        raise ValueError(f"choose 1–{count} or Enter") from exc
    if number is not None and not 1 <= number <= count:
        raise ValueError(f"choose 1–{count} or Enter")
    print("\n" + trace_map(text, key, passes, number) + "\n")


def numbered_menu() -> int:
    print("\nDRAGON CURVE — Geometric Transposition")
    print("Type :back at any prompt to return here.")
    while True:
        print("\n  1. Encrypt text (modern Character mode)")
        print("  2. Decrypt text (modern Character mode)")
        print("  3. Encrypt a file — Character / Hex / Byte / Bit")
        print("  4. Decrypt a file — DATA/3 autodetection")
        print("  5. Show a Dragon Curve map")
        print("  6. Encrypt a folder — ZIP package + optional weave")
        print("  7. Decrypt a folder — restore complete tree")
        print("  0. Exit")
        try:
            choice = _ask("\nChoose 0–7: ").strip()
            if choice == "0":
                print("Goodbye.")
                return 0
            if choice == "1": _menu_text(False)
            elif choice == "2": _menu_text(True)
            elif choice == "3": _menu_binary_file(False)
            elif choice == "4": _menu_binary_file(True)
            elif choice == "5": _menu_map()
            elif choice == "6": _menu_folder(False)
            elif choice == "7": _menu_folder(True)
            else: print("Please choose 0–7")
        except _MenuBack:
            continue
        except _MenuExit:
            print("\nGoodbye.")
            return 0
        except (ValueError, TypeError, RuntimeError, OSError,
                UnicodeError, zipfile.BadZipFile) as exc:
            print(f"Could not complete operation: {exc}")


def _text_cli(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Modern Dragon Curve text transposition")
    parser.add_argument("words", nargs="*")
    parser.add_argument("--text", action="store_true", help="force literal text input")
    parser.add_argument("--decrypt", "-d", action="store_true")
    parser.add_argument("--key", "-k", default="")
    parser.add_argument("--passes", type=int)
    parser.add_argument("--map", "-m", action="store_true")
    parser.add_argument("--map-pass", type=int)
    parser.add_argument("--lines", action="store_true", help="process lines interactively")
    opts = parser.parse_args(argv)
    if opts.map_pass is not None and not opts.map:
        parser.error("--map-pass requires --map")
    if opts.passes is not None and not 1 <= opts.passes <= 7:
        parser.error("--passes must be 1–7")
    if opts.map_pass is not None and not 1 <= opts.map_pass <= len(_selected_plan(opts.key, opts.passes)):
        parser.error("--map-pass outside selected pass range")
    if opts.lines and opts.words:
        parser.error("--lines does not accept positional words")
    def process(message: str) -> str:
        result = decrypt(message, opts.key, opts.passes) if opts.decrypt else encrypt(message, opts.key, opts.passes)
        if opts.map:
            _emit_map(result if opts.decrypt else message, opts.key, opts.passes, opts.map_pass)
        return result
    try:
        if opts.lines and sys.stdin.isatty():
            while True:
                try:
                    print(process(input("> ")))
                except EOFError:
                    return 0
        if opts.words:
            data = " ".join(opts.words)
            print(process(data))
        else:
            data = sys.stdin.read()
            sys.stdout.write(process(data))
            sys.stdout.flush()
    except (ValueError, UnicodeError, OSError) as exc:
        parser.error(str(exc))
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__.strip())
        return 0
    if args == ["--menu"] or (not args and sys.stdin.isatty()):
        return numbered_menu()
    if any(a == "--folder" or a.startswith("--folder=") for a in args):
        return _folder_cli(args)
    # Preserve --file calls (with or without --unit) and positional paths.
    if (any(a in ("--file", "--unit", "--scramble-name", "--no-header") or
            a.startswith(("--file=", "--unit=")) for a in args)):
        return _binary_cli(args)
    if len(args) == 1 and Path(args[0]).expanduser().is_file():
        return _binary_cli(["--file", args[0]])
    if len(args) == 2 and args[0] == "--decrypt" and Path(args[1]).expanduser().is_file():
        return _binary_cli(["--decrypt", "--file", args[1]])
    return _text_cli(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
