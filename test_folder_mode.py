"""Real folder packaging, restoration, backwards compatibility and safety."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import dragon_curve as dc


class FolderTests(unittest.TestCase):
    def make_tree(self, parent):
        root = parent / '🐉 Dragon Lab'
        (root / 'Subfolder' / 'Empty').mkdir(parents=True)
        (root / '.hidden').write_bytes(b'\x00\xff\x01Private!\x7f')
        (root / 'Readme.md').write_text('Unicode: café\nGreek: αβγ\n', encoding='utf-8')
        (root / 'Subfolder' / 'photo.png').write_bytes(b'\x89PNG\r\n\x1a\n' + bytes(range(256)))
        (root / 'zero-length.bin').touch()
        (root / 'empty').mkdir()
        return root

    def tree_snapshot(self, root):
        state = {}
        for item in root.rglob('*'):
            p = item.relative_to(root).as_posix()
            state[p + ('/' if item.is_dir() else '')] = None if item.is_dir() else hashlib.sha256(item.read_bytes()).hexdigest()
        return state

    def test_round_trip_modes_all_7_passes_and_nested_empty(self):
        for mode in ('byte', 'hex', 'bit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                parent = Path(tmp)
                root = self.make_tree(parent)
                original = self.tree_snapshot(root)
                pkg = dc.encode_folder(root, key='Dragon_Curve', unit=mode,
                                       passes=7, scramble_name=True)
                self.assertTrue(pkg.name.startswith('dc-'))
                self.assertEqual(pkg.suffix, '.dragon')
                self.assertFalse(pkg.read_bytes().startswith(b'PK\x03\x04'))
                self.assertTrue(pkg.read_bytes().startswith(dc.FOLDER_PREFIX))
                root.rename(parent / 'original moved')
                restored = dc.decode_folder(pkg, key='Dragon_Curve')
                self.assertEqual(restored, root)
                self.assertEqual(self.tree_snapshot(restored), original)

    def test_no_key_one_pass_and_custom_folder_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            pkg = dc.encode_folder(src, unit='byte', scramble_name=False)
            self.assertEqual(pkg.name, src.name + '.dragon')
            dst = parent / 'Restore target'
            self.assertEqual(dc.decode_folder(pkg, output=dst), dst)
            self.assertEqual(self.tree_snapshot(src), self.tree_snapshot(dst))
            self.assertIn(b'passes=1 key=0', pkg.read_bytes()[:120])

    def test_corruption_and_wrong_key_leave_no_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            pkg = dc.encode_folder(src, key='right', passes=4, unit='hex')
            src.rename(parent / 'moved original')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                dc.decode_folder(pkg, key='wrong')
            self.assertFalse(src.exists())
            data = pkg.read_bytes()
            header, payload = data.split(b'\n', 1)
            pkg.write_bytes(header + b'\n' + payload[:-1] + bytes([payload[-1] ^ 0xff]))
            with self.assertRaises(ValueError):
                dc.decode_folder(pkg, key='right')
            self.assertFalse(src.exists())

    def test_never_overwrite_existing_folder_or_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            pkg = dc.encode_folder(src, key='k')
            with self.assertRaises(OSError):
                dc.encode_folder(src, output=pkg, key='k')
            with self.assertRaises(OSError):
                dc.decode_folder(pkg, key='k')
            self.assertTrue((src / 'Readme.md').exists())

    def test_nested_destination_inside_source_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_tree(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'outside'):
                dc.encode_folder(root, output=root / 'bad.dragon')
            self.assertFalse((root / 'bad.dragon').exists())

    @unittest.skipIf(os.name == 'nt', 'symlinks not always available')
    def test_symlink_refused_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            (src / 'tricky').symlink_to(src / 'Readme.md')
            pkg = parent / 'output.dragon'
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                dc.encode_folder(src, output=pkg)
            self.assertFalse(pkg.exists())

    def test_multiple_chunks_exact_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            source = parent / 'big-data'
            source.mkdir()
            # Incompressible, crosses multiple byte-mode chunk boundaries.
            payload = bytes(range(256)) * 110 + os.urandom(38000)
            (source / 'data.dat').write_bytes(payload)
            pkg = dc.encode_folder(source, key='chunk-test', passes=4, unit='byte')
            output = dc.decode_folder(pkg, output=parent / 'recovered', key='chunk-test')
            self.assertEqual((output / 'data.dat').read_bytes(), payload)

    def test_zip_store_and_deflate_are_reversible_in_all_units(self):
        for mode in ('byte', 'hex', 'bit'):
            for method in ('deflate', 'store'):
                with self.subTest(mode=mode, method=method), tempfile.TemporaryDirectory() as tmp:
                    parent = Path(tmp)
                    src = self.make_tree(parent)
                    # Ensure compressibility is measurable and that we also
                    # exercise binary, UTF-8 names, and empty subdirectories.
                    (src / 'large.txt').write_bytes(b'REPEATING TEXT ' * 3000)
                    snapshot = self.tree_snapshot(src)
                    pkg = dc.encode_folder(src, key='pack-test', passes=7,
                                           unit=mode, compression=method,
                                           scramble_name=True)
                    self.assertEqual(pkg.suffix, '.dragon')
                    with tempfile.TemporaryDirectory() as scratch:
                        zp = Path(scratch) / 'raw.zip'
                        dc._unpack_folder(pkg, 'pack-test', zp)
                        with zipfile.ZipFile(zp) as z:
                            for member in z.infolist():
                                if not member.is_dir():
                                    self.assertEqual(member.compress_type,
                                        dc.FOLDER_COMPRESSIONS[method])
                    src.rename(parent / 'original kept safe')
                    restored = dc.decode_folder(pkg, key='pack-test')
                    self.assertEqual(restored.name, src.name)
                    self.assertEqual(self.tree_snapshot(restored), snapshot)

    def test_store_is_larger_and_default_deflate_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = parent / 'repetitive'
            src.mkdir()
            (src / 'many.txt').write_bytes(b'abc 123\n' * 20000)
            compressed = dc.encode_folder(src, output=parent / 'deflate.dragon',
                                          key='k', unit='byte', passes=3)
            stored = dc.encode_folder(src, output=parent / 'store.dragon',
                                      key='k', unit='byte', passes=3,
                                      compression='store')
            self.assertGreater(stored.stat().st_size, compressed.stat().st_size * 4)
            for package, expected_method in ((compressed, zipfile.ZIP_DEFLATED),
                                             (stored, zipfile.ZIP_STORED)):
                with tempfile.TemporaryDirectory() as scratch:
                    zpath = Path(scratch) / 'raw.zip'
                    dc._unpack_folder(package, 'k', zpath)
                    with zipfile.ZipFile(zpath) as archive:
                        self.assertEqual(archive.getinfo('repetitive/many.txt').compress_type,
                                         expected_method)
            # No header version change: new Store files are readable by
            # previous folder decoders, which use the ZIP's per-entry method.
            self.assertEqual(compressed.read_bytes().split(b'\n', 1)[0].split(b' ')[0],
                             stored.read_bytes().split(b'\n', 1)[0].split(b' ')[0])
            self.assertTrue(stored.read_bytes().startswith(dc.FOLDER_PREFIX))

    def test_invalid_compression_is_rejected_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            source = self.make_tree(parent)
            target = parent / 'bad.dragon'
            with self.assertRaisesRegex(ValueError, 'compression'):
                dc.encode_folder(source, output=target, compression='lzma')
            self.assertFalse(target.exists())

    def test_store_menu_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            answers = iter(['6', str(src), '2', '1', '4', 'menu-key', 'n', 'n', '', '0'])
            output = io.StringIO()
            with patch('builtins.input', side_effect=lambda _: next(answers)):
                with redirect_stdout(output):
                    self.assertEqual(dc.numbered_menu(), 0)
            pkg = parent / (src.name + '.dragon')
            self.assertTrue(pkg.exists())
            with tempfile.TemporaryDirectory() as scratch:
                archive_path = Path(scratch) / 'zip'
                dc._unpack_folder(pkg, 'menu-key', archive_path)
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(archive.getinfo(src.name + '/Readme.md').compress_type,
                                     zipfile.ZIP_STORED)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['dragon_curve.py', '--folder', str(src),
                                          '--key', 'cli-key', '--compression', 'store',
                                          '--output', str(parent / 'cli.dragon')]), 0)
            with tempfile.TemporaryDirectory() as scratch:
                archive_path = Path(scratch) / 'zip'
                dc._unpack_folder(parent / 'cli.dragon', 'cli-key', archive_path)
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(archive.getinfo(src.name + '/Readme.md').compress_type,
                                     zipfile.ZIP_STORED)

    def test_corrupt_zip_entry_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            z = parent / 'test.zip'
            with zipfile.ZipFile(z, 'w') as out:
                out.writestr('folder/', '')
                out.writestr('folder/../evil.txt', 'oops')
            with zipfile.ZipFile(z) as archive:
                with self.assertRaises(ValueError):
                    dc._safe_folder_zip_members(archive)

    def test_menu_encrypt_and_decrypt(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            src = self.make_tree(parent)
            # Run actual menu: choose 6 and defaults, then exit.
            answers = iter(['6', str(src), '', '', '7', 'phrase', 'n', 'y', '', '0'])
            with patch('builtins.input', side_effect=lambda _: next(answers)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(dc.numbered_menu(), 0)
            pkg = next(parent.glob('dc-*.dragon'))
            src.rename(parent / 'old files')
            answers = iter(['7', str(pkg), 'phrase', '', '0'])
            with patch('builtins.input', side_effect=lambda _: next(answers)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(dc.numbered_menu(), 0)
            self.assertEqual(self.tree_snapshot(src), self.tree_snapshot(parent / 'old files'))

    def test_folder_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = self.make_tree(parent)
            output = parent / 'bundle.dc'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['dragon_curve.py', '--folder', str(root),
                                          '--key', 'k', '--passes', '4',
                                          '--unit', 'byte', '--scramble-name',
                                          '--output', str(output)]), 0)
            result = parent / 'restored'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['dragon_curve.py', '--decrypt',
                                          '--folder', str(output), '--key', 'k',
                                          '--output', str(result)]), 0)
            self.assertEqual(self.tree_snapshot(root), self.tree_snapshot(result))


if __name__ == '__main__':
    unittest.main()
