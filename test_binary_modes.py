import unittest
import io
from contextlib import redirect_stdout, redirect_stderr
import tempfile
from pathlib import Path
from dragon_curve import UNITS, transform_units, encode_data, decode_data, main

class BinaryModeTests(unittest.TestCase):
    def test_all_modes_and_passes(self):
        for unit in UNITS:
            plain = 'Hello 🐉 ω café'.encode() if unit == 'character' else bytes(range(256))
            for key in ('', '42', '98904798123745987145'):
                for passes in range(1, 8):
                    with self.subTest(unit=unit,key=key,passes=passes):
                        encrypted = encode_data(plain,key,unit,passes)
                        self.assertEqual(decode_data(encrypted,key),plain)
                        self.assertEqual(transform_units(transform_units(plain,key,unit,passes),key,unit,passes,True),plain)
    def test_binary_nul_ff_and_empty(self):
        for unit in ('bit','hex','byte'):
            for plain in (b'',b'\x00\xff\x00\x01',bytes(range(256))):
                self.assertEqual(decode_data(encode_data(plain,'test',unit,7),'test'),plain)
    def test_invalid_utf8(self):
        with self.assertRaises(UnicodeDecodeError):
            encode_data(b'\xff',unit='character')
    def test_malformed_header(self):
        with self.assertRaises(ValueError):
            decode_data(b'DRAGON-CURVE-DATA/3 mode=unknown passes=4 key=0\n')
    def test_file_cli(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'photo.bin';mid=Path(d)/'photo.dragon.bin';dst=Path(d)/'recovered.bin'
            plain=bytes(range(256))+b'\x00\xff'
            src.write_bytes(plain)
            self.assertEqual(main(['dragon_curve.py','--unit','bit','--passes','4','--key','x','--file',str(src)]),0)
            self.assertTrue(mid.exists())
            self.assertEqual(main(['dragon_curve.py','--unit','byte','--decrypt','--key','x','--file',str(mid),'--output',str(dst)]),0)
            self.assertEqual(dst.read_bytes(),plain)

    def test_character_unit_cli_map_follows_seven_passes_and_header(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/'source.txt'
            encoded = Path(d)/'source.dragon.txt'
            restored = Path(d)/'restored.txt'
            original = 'A map with seven passes, café 🐉'.encode()
            src.write_bytes(original)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(['dragon_curve.py', '--unit', 'character',
                                       '--passes', '7', '--key', 'paper', '--map',
                                       '--map-pass', '7', '--file', str(src)]), 0)
            self.assertIn('PASS 7/7', stderr.getvalue())
            self.assertNotIn('PASS 1/7', stderr.getvalue())
            self.assertTrue(encoded.read_bytes().startswith(
                b'DRAGON-CURVE-DATA/3 mode=character passes=7 key=1\n'))
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(['dragon_curve.py', '--unit', 'character',
                                       '--decrypt', '--key', 'paper', '--map',
                                       '--file', str(encoded), '--output', str(restored)]), 0)
            self.assertEqual(restored.read_bytes(), original)
            self.assertIn('PASS 7/7', stderr.getvalue())
            self.assertEqual(stderr.getvalue().count('\nPASS '), 7)

    def test_byte_mode_rejects_map(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/'x.bin'
            src.write_bytes(bytes([0, 255]))
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(['dragon_curve.py', '--unit', 'byte', '--passes', '7',
                          '--map', '--file', str(src)])
            self.assertFalse((Path(d)/'x.dragon.bin').exists())

if __name__=='__main__':unittest.main()
