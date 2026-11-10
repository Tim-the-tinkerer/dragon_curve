"""Cross-chunk weave reversibility, actual spatial mixing, and compatibility."""
from pathlib import Path
import hashlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import zipfile
import dragon_curve as dc


class CrossChunkWeaveTests(unittest.TestCase):
    @staticmethod
    def example_folder(parent):
        folder = parent / '🐉 Archive Samples'
        (folder / 'empty subfolder').mkdir(parents=True)
        (folder / 'alpha.txt').write_bytes(b'alpha-shakespeare ' * 12000)
        (folder / 'beta.bin').write_bytes(bytes(range(256)) * 143)
        (folder / 'Ω.txt').write_text('Unicode name, café, Δ, 漢字\n' * 120,
                                         encoding='utf-8')
        (folder / '.hidden').write_bytes(b'\x00\xff\x01')
        return folder

    @staticmethod
    def snapshot(folder):
        return {p.relative_to(folder).as_posix() + ('/' if p.is_dir() else ''):
                None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob('*')}

    def test_store_deflate_byte_hex_bit_roundtrip_and_header(self):
        for unit in dc.FOLDER_MODES:
            for compression in ('store', 'deflate'):
                with self.subTest(unit=unit, compression=compression), tempfile.TemporaryDirectory() as tmp:
                    parent = Path(tmp)
                    original = self.example_folder(parent)
                    snapshot = self.snapshot(original)
                    encoded = dc.encode_folder(original, key='Dragon_Curve', unit=unit,
                                               passes=7, compression=compression,
                                               cross_weave=True, scramble_name=True)
                    raw = encoded.read_bytes()
                    self.assertTrue(raw.startswith(dc.FOLDER_WEAVE_PREFIX))
                    header = raw.split(b'\n', 1)[0]
                    self.assertIn(b' weave=16', header)
                    self.assertIn(b' passes=7', header)
                    self.assertTrue(encoded.name.startswith('dc-'))
                    original.rename(parent / 'moved original')
                    restored = dc.decode_folder(encoded, key='Dragon_Curve')
                    self.assertEqual(restored, original)
                    self.assertEqual(self.snapshot(restored), snapshot)

    def test_cross_region_data_really_gets_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            chunk = dc._FOLDER_CHUNK_BYTES['byte']
            source = folder / 'original'
            dest = folder / 'woven'
            result = folder / 'restored'
            # Region A entirely precedes region B. No compression involved.
            original = b'A' * (chunk * 64) + b'B' * (chunk * 64)
            source.write_bytes(original)
            dc._folder_weave_file(source, dest, 'Dragon_Curve', 7, chunk, len(original))
            sample = dest.read_bytes()
            self.assertEqual(len(sample), len(original))
            self.assertEqual(sorted(sample), sorted(original))
            # Every non-edge chunk samples both remote halves of the archive.
            mixed = sum(b'A' in sample[i:i+chunk] and b'B' in sample[i:i+chunk]
                        for i in range(0, len(sample), chunk))
            self.assertGreater(mixed, 110, f'Only {mixed}/128 chunks contain both sources')
            dc._folder_weave_file(dest, result, 'Dragon_Curve', 7, chunk, len(original),
                                  reverse=True)
            self.assertEqual(result.read_bytes(), original)

    def test_padding_roundtrip_and_short_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for unit in dc.FOLDER_MODES:
                for length in (1, 13, 256, 2049, 16411):
                    with self.subTest(unit=unit, length=length):
                        chunk = dc._FOLDER_CHUNK_BYTES[unit]
                        original = os.urandom(length)
                        source = folder / 'original'
                        woven = folder / 'woven'
                        restored = folder / 'restored'
                        source.write_bytes(original)
                        dc._folder_weave_file(source, woven, 'Key', 7, chunk, length)
                        self.assertEqual(woven.stat().st_size,
                                         (length + chunk - 1)//chunk * chunk)
                        dc._folder_weave_file(woven, restored, 'Key', 7, chunk, length,
                                              reverse=True)
                        self.assertEqual(restored.read_bytes(), original)

    def test_wrong_key_or_corruption_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            src = self.example_folder(folder)
            pkg = dc.encode_folder(src, key='correct', passes=7,
                                   unit='bit', compression='store', cross_weave=True)
            src.rename(folder / 'original moved')
            with self.assertRaisesRegex(ValueError, 'wrong key|padding damaged'):
                dc.decode_folder(pkg, key='incorrect')
            self.assertFalse(src.exists())
            raw = bytearray(pkg.read_bytes())
            raw[-155] ^= 1
            broken = folder / 'corrupt.dragon'
            broken.write_bytes(raw)
            with self.assertRaises(ValueError):
                dc.decode_folder(broken, key='correct')
            self.assertFalse(src.exists())

    def test_default_header_stays_v1_and_decodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            src = self.example_folder(folder)
            pkg = dc.encode_folder(src, key='key', passes=4,
                                   unit='hex', compression='store')
            self.assertTrue(pkg.read_bytes().startswith(dc.FOLDER_PREFIX))
            self.assertNotIn(b'weave=', pkg.read_bytes().split(b'\n',1)[0])
            restored = dc.decode_folder(pkg, output=folder / 'copy', key='key')
            self.assertEqual(self.snapshot(src), self.snapshot(restored))

    def test_menu_prompt_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            src = self.example_folder(folder)
            # 6 / path / Store / Byte / 7 passes / key / weave yes / name no / default
            answers = iter(['6', str(src), '2', '1', '7', 'key', 'y', 'n', '', '0'])
            with patch('builtins.input', side_effect=lambda _: next(answers)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(dc.numbered_menu(), 0)
            package = folder / (src.name + '.dragon')
            self.assertTrue(package.read_bytes().startswith(dc.FOLDER_WEAVE_PREFIX))
            via_cli = folder / 'cli.dragon'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['dragon_curve.py', '--folder', str(src),
                                          '--key', 'Key2', '--cross-weave',
                                          '--compression', 'store', '--unit', 'hex',
                                          '--passes', '7', '--output', str(via_cli)]), 0)
            self.assertTrue(via_cli.read_bytes().startswith(dc.FOLDER_WEAVE_PREFIX))
            result = folder / 'cli-result'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['dragon_curve.py', '--folder', str(via_cli),
                                          '--decrypt', '--key', 'Key2',
                                          '--output', str(result)]), 0)
            self.assertEqual(self.snapshot(src), self.snapshot(result))
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    dc.main(['dragon_curve.py', '--folder', str(via_cli),
                             '--decrypt', '--cross-weave'])

    def test_v2_header_rejects_missing_weave_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self.example_folder(folder)
            pkg = dc.encode_folder(source, key='k', cross_weave=True)
            data = pkg.read_bytes().replace(b' weave=16', b'', 1)
            damaged = folder / 'bad.dragon'
            damaged.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'weave format'):
                dc.decode_folder(damaged, output=folder / 'restored', key='k')
            self.assertFalse((folder / 'restored').exists())


if __name__ == '__main__':
    unittest.main()
