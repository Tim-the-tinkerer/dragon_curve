# Dragon Curve — Geometric Transposition

A small, deliberately impractical laboratory for reversible file scrambling using the Heighway dragon curve. It can transpose text, binary files, and complete folders; optionally obscure output filenames; and mix distant regions of a folder with **Cross-Chunk Weave**.

**This is obfuscation, not secure encryption.** It does not provide cryptographic confidentiality or integrity guarantees for ordinary files. Folder packages include a checksum for detecting corruption/wrong keys, but a checksum is not authentication.

## Starting the program

Run `Dragon Curve.command` on macOS, `Dragon Curve.bat` on Windows, or:

```sh
python3 dragon_curve.py
```

The menu opens automatically when run at a terminal with no arguments, or explicitly with `python3 dragon_curve.py --menu`.

```text
1. Encrypt text (modern Character mode)
2. Decrypt text (modern Character mode)
3. Encrypt a file — Character / Hex / Byte / Bit
4. Decrypt a file — DATA/3 autodetection
5. Show a Dragon Curve map
6. Encrypt a folder — ZIP package + optional weave
7. Decrypt a folder — restore complete tree
0. Exit
```

Enter `:back` at any prompt to return to the menu. Use drag-and-drop file paths or type the path manually. Output files and folders are **not silently overwritten**.

### Current supported formats

| Format | Purpose | Status |
|---|---|---|
| `DRAGON-CURVE-DATA/3` | Character, Hex, Byte, and Bit file transformations | Supported |
| `DRAGON-CURVE-FOLDER/1` | Folder package without cross-chunk weave | Supported |
| `DRAGON-CURVE-FOLDER/2` | Folder package with cross-chunk weave | Supported |
| `DRAGON-CURVE-TEXT/1` | Old versioned text envelope | **Retired** |
| `DRAGON-CURVE-TEXT/2` | Old versioned text envelope | **Retired** |

**Important:** The `1` and `2` in *folder* formats do not refer to the retired *text* formats. Both folder formats are deliberately retained so existing `.dragon` packages can still be restored. Disabling Cross-Chunk Weave continues to produce `FOLDER/1`; enabling it produces `FOLDER/2`.

The old `--classic` flag, `--header` flag, and `encrypt_document` / `decrypt_document` / `header_details` text-envelope functions are removed. Versioned `TEXT/1` and `TEXT/2` files now receive an explicit unsupported-format error rather than being silently treated as DATA/3. **Keep a previous release** if you want to restore old versioned text files; after recovering their plaintext, use the modern Character file mode to create new `DATA/3` files. No files are converted automatically.

Unmarked raw character transpositions cannot reliably declare what version created them; modern text input/output has no `TEXT/1` or `TEXT/2` envelope and uses the current Character-mode algorithm. Supply the same key and optional number of passes to reverse it.

## Text and file modes

For short text, menu options **1–2** apply the Character unit's geometric permutation. Input/output remains ordinary characters without version metadata. Enter the same key and pass count to undo the operation. By default, the automatic pass count is one pass without a key and four to seven keyed passes.

File modes **3–4** use `DATA/3` by default and detect the unit and number of passes automatically on decode. Units:

- **Character:** reorder complete Unicode characters (valid UTF-8 required).
- **Hex:** reorder individual four-bit nibbles of each byte.
- **Byte:** reorder complete bytes; appropriate for arbitrary files.
- **Bit:** reorder individual bits; typically slower and produces different byte distributions.

Each mode is a reversible permutation and requires the same original key. A different key generally yields an unusable reconstruction. For file mode, optional **Scramble output filename** stores a reversibly obfuscated basename/extension in the header and writes a neutral filename such as `dc-...dragon`. Decryption automatically restores the original filename when it's free.

### Text CLI

```sh
python3 dragon_curve.py --text 'Hello' --key paper
python3 dragon_curve.py --decrypt --text 'loeHl' --key paper
python3 dragon_curve.py --text 'A longer message' --key paper --passes 7
python3 dragon_curve.py --text 'A longer message' --key paper --passes 7 --map
python3 dragon_curve.py --text 'A longer message' --key paper --passes 7 --map --map-pass 7
```

Text entered directly is unversioned; it is *not* a `DRAGON-CURVE-TEXT/2` file. A noninteractive pipe processes the incoming text as a single message. `--lines` opens an interactive one-line-at-a-time prompt when stdin is a terminal.

### File CLI

```sh
# Current binary-capable format; use an explicit mode when encoding.
python3 dragon_curve.py --file sample.bin --unit bit --passes 7 --key Dragon_Curve

# Decode (unit and passes detected from the DATA/3 header).
python3 dragon_curve.py --file sample.dragon.bin --decrypt --key Dragon_Curve

# Preserve the current character and filename scrambling features.
python3 dragon_curve.py --file notes.txt --unit character --passes 7 --key Dragon_Curve --scramble-name

# Choose the output path explicitly (must not exist already).
python3 dragon_curve.py --file image.png --unit hex --key example --output image.gear
```

If `--unit` is omitted for **file encoding**, Byte is the default. `--file` with no `--unit` is supported. `--no-header` is available for experimental raw file output, but such a file cannot self-describe its mode or pass count; provide both explicitly when decoding. Filename scrambling requires a header. The normal `DATA/3` header doesn't record the key itself, only whether one was used.

The `--map` visualization applies only to Character mode, where the display can trace visible characters along the curve. It does not alter the resulting file.

## Folder mode

The folder workflow creates one `.dragon` package from a directory tree. It preserves files, subfolders, empty directories, and binary data. Folder mode has three separate settings:

1. **ZIP method:** Deflate (compress, default) or Store (no compression).
2. **Transposition unit:** Byte (default), Hex, or Bit, and an optional explicit 1–7 passes.
3. **Cross-Chunk Weave:** optional, off by default. Optionally scramble the output filename as well.

**Packaging order:** ZIP the folder → optionally weave stripes between distant chunks → apply the per-chunk dragon curve permutations → save a folder package. Decoding reverses these steps. ZIP stores each member's original path and compression method; decoding automatically reads either Store or Deflate without asking.

**Store** is useful when studying the raw byte distribution. **Deflate** is useful when smaller packages matter. Dragon Curve does not itself compress data; the ZIP stage does.

### Cross-Chunk Weave

With weaving disabled, Dragon Curve transposes each bounded chunk independently, leaving large-scale regions of the archive recognizable statistically. Enabling the weave distributes each chunk's **16 stripes** across distant chunks before the usual transposition. The weave is deterministic, key-dependent, reversible, and disk-backed so large archives don't have to fit in RAM. The final partial chunk is zero-padded for weaving, then trimmed during restoration. This can make woven Store packages larger than unwoven packages.

The option changes folder metadata to `DRAGON-CURVE-FOLDER/2`; it does **not** change the existing `DATA/3` file format or `FOLDER/1` behavior. Old `FOLDER/1` packages continue to decode as before.

### Folder CLI

```sh
# Deflate by default; no weave, Folder/1 format.
python3 dragon_curve.py --folder 'My Collection' --unit byte --key Dragon_Curve

# Store with cross-chunk weave, folder format 2.
python3 dragon_curve.py --folder 'My Collection' --compression store --cross-weave --unit bit --passes 7 --key Dragon_Curve

# Also scramble the package's visible filename.
python3 dragon_curve.py --folder 'My Collection' --compression store --cross-weave --scramble-name --key Dragon_Curve

# Recover into original folder name (only if unused).
python3 dragon_curve.py --folder 'My Collection.dragon' --decrypt --key Dragon_Curve

# Recover into a specified new folder path.
python3 dragon_curve.py --folder archive.dragon --decrypt --key Dragon_Curve --output 'Recovered Collection'
```

The folder decoder auto-detects `FOLDER/1` or `FOLDER/2`, unit, and passes. Compression is read from the internal ZIP. **Do not** use `--cross-weave`, `--unit`, `--compression`, or `--scramble-name` when decrypting: the settings are already in the package.

Folders are not replaced or deleted. Symlinks and special files are rejected. Relative path safety and folder-root names are checked when restoring. Exact file permissions, ownership, timestamps, and additional macOS filesystem metadata aren't guaranteed; `.DS_Store` files, when present, are ordinary preserved files.

## Testing

Run the maintained suite:

```sh
python3 -m unittest discover -p 'test_*.py' -q
```

This covers geometric permutation, modern Character text, DATA/3, filename scrambling, Store/Deflate, Folder/1 and Folder/2 recovery, Cross-Chunk Weave, Unicode names, wrong-key/corruption behavior, empty directories, and no-overwrite rules.

## Limits and cautions

- This program is for experiments and creative data manipulation, **not protection of sensitive files**.
- The file (non-folder) modes work in memory; very large files may need substantial memory.
- Folder mode uses bounded transformation chunks and temporary disk files. Cross-chunk weaving also performs random disk writes and may be slower than ordinary folder processing.
- File mode doesn't authenticate data. Without scrambled-name metadata, an incorrect key can yield nonsense without an error.
- Continue keeping a copy of the previous release if you have important `DRAGON-CURVE-TEXT/1` or `/2` messages.
