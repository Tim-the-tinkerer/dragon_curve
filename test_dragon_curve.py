#!/usr/bin/env python3
"""Modern Dragon Curve geometry, text, menu, and compatibility tests."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch
import dragon_curve as dc


class GeometryTests(unittest.TestCase):
    def test_fnv_vectors(self):
        self.assertEqual(dc.fnv1a_64(b''), 0xCBF29CE484222325)
        self.assertEqual(dc.fnv1a_64(b'a'), 0xAF63DC4C8601EC8C)
        self.assertEqual(dc.fnv1a_64(b'foobar'), 0x85944171F73967E8)

    def test_turns(self):
        expected = [1,1,-1,1,1,-1,-1,1,1,1,-1,-1,1,-1,-1,1]
        self.assertEqual([dc.paper_turn(n) for n in range(1,17)], expected)
        with self.assertRaises(ValueError): dc.paper_turn(0)
        with self.assertRaises(TypeError): dc.paper_turn(True)

    def test_points_and_reading_orders(self):
        self.assertEqual(dc.midpoints(5), [(1,0),(2,1),(1,2),(0,3),(-1,4)])
        pts = dc.midpoints(200)
        self.assertEqual(len(set(pts)), 200)
        orders = [tuple(dc.reading_order(pts,v)) for v in range(8)]
        self.assertEqual(len(set(orders)), 8)
        for order in orders:
            self.assertEqual(sorted(order), list(range(200)))

    def test_keyed_plan_count(self):
        self.assertEqual(dc.pass_plan(''),[(1,0,0,0)])
        self.assertEqual(len(dc.pass_plan('paper')),7)
        self.assertEqual({len(dc.pass_plan(f'key{i}')) for i in range(100)}, {4,5,6,7})
        self.assertEqual(dc.splitmix64(0)[1],0xE220A8397B1DCDAF)

    def test_known_text_vectors(self):
        self.assertEqual(dc.encrypt('Hello'),'olleH')
        self.assertEqual(dc.encrypt('Hello','paper'),'loeHl')
        self.assertEqual(dc.decrypt('loeHl','paper'),'Hello')
        self.assertEqual(dc.encrypt('Hello, World!','paper'),'!Wl, Hedrlloo')

    def test_unicode_text_and_explicit_passes(self):
        samples=['', '🐉 café\nA B\t', 'あいうえお', 'Hello!']
        for text in samples:
            for key in ('','Dragon_Curve'):
                for count in (None,1,4,7):
                    with self.subTest(text=text,key=key,count=count):
                        encoded=dc.encrypt(text,key,count)
                        self.assertEqual(sorted(encoded),sorted(text))
                        self.assertEqual(dc.decrypt(encoded,key,count),text)
                        self.assertEqual(encoded.encode(), dc.transform_units(text.encode(),key,'character',count))

    def test_map_matches_current_character_transform(self):
        sample='A Dragon Curve Test'
        for key,count in (('',1),('paper',None),('paper',7)):
            track=dc.trace_map(sample,key,count)
            self.assertIn('DATA/3 character mode',track)
            self.assertIn('PASS 1/',track)
            self.assertTrue(dc.curve_map(sample,key,1,count))
        with self.assertRaises(ValueError): dc.trace_map('abc',passes=1,pass_number=5)

    def test_character_mode_retired_headers_rejected(self):
        for version in (1,2):
            with self.assertRaisesRegex(ValueError, 'retired'):
                dc.decrypt(f'DRAGON-CURVE-TEXT/{version} key=0\nolleH')
            with self.assertRaisesRegex(ValueError, 'retired'):
                dc.decode_data(f'DRAGON-CURVE-TEXT/{version} key=0\nolleH'.encode())

    def test_no_old_format_routines(self):
        for old in ('encrypt_document','decrypt_document','header_details','_text_header_info'):
            self.assertFalse(hasattr(dc,old))


class ModernCliTests(unittest.TestCase):
    @staticmethod
    def call(*args):
        stdout=io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(io.StringIO()):
            code=dc.main(['dragon_curve.py',*args])
        return code,stdout.getvalue()

    def test_help_documents_active_formats(self):
        code,out=self.call('--help')
        self.assertEqual(code,0)
        self.assertIn('DATA/3',out)
        self.assertIn('FOLDER/1',out)
        self.assertIn('FOLDER/2',out)

    def test_text_command_line(self):
        self.assertEqual(self.call('Hello'),(0,'olleH\n'))
        self.assertEqual(self.call('--decrypt','olleH'),(0,'Hello\n'))
        self.assertEqual(self.call('--key','paper','Hello'),(0,'loeHl\n'))
        encoded=dc.encrypt('🐉 Hello','test',7)
        self.assertEqual(self.call('--decrypt','--key','test','--passes','7','--text',encoded),
                         (0,'🐉 Hello\n'))

    def test_text_from_pipe_preserves_newline(self):
        stream=io.StringIO('Hello\nDragon')
        with patch('sys.stdin',stream):
            code,result=self.call('--key','paper')
        self.assertEqual(code,0)
        self.assertEqual(dc.decrypt(result,'paper'),'Hello\nDragon')

    def test_old_flags_are_no_longer_accepted(self):
        for flag in ('--classic','--header'):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    dc.main(['dragon_curve.py',flag,'Hello'])

    def test_file_default_uses_data_3_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'text.txt'; original='Hello 🐉\nMore text!'.encode()
            src.write_bytes(original)
            code,_=self.call('--file',str(src),'--key','paper','--unit','character')
            self.assertEqual(code,0)
            encoded=Path(tmp)/'text.dragon.txt'
            self.assertTrue(encoded.read_bytes().startswith(b'DRAGON-CURVE-DATA/3 mode=character'))
            src.unlink()
            code,_=self.call('--decrypt','--file',str(encoded),'--key','paper')
            self.assertEqual(code,0)
            self.assertEqual(src.read_bytes(),original)

    def test_retired_text_file_is_not_decoded_or_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'old.txt';source.write_text('DRAGON-CURVE-TEXT/2 key=1\nABC')
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    dc.main(['x','--file',str(source),'--decrypt','--key','test'])
            self.assertEqual(source.read_text(),'DRAGON-CURVE-TEXT/2 key=1\nABC')
            self.assertFalse((Path(tmp)/'old.plain.txt').exists())

    def test_menu_text_and_file_choices(self):
        answers=iter(['1','Hello','', '', '2','olleH','', '', '0'])
        output=io.StringIO()
        with patch('builtins.input', side_effect=lambda _: next(answers)):
            with redirect_stdout(output):
                self.assertEqual(dc.numbered_menu(),0)
        self.assertIn('DATA/3 autodetection',output.getvalue())
        self.assertIn('Result:\nolleH',output.getvalue())
        self.assertIn('Result:\nHello',output.getvalue())
        self.assertNotIn('legacy v1/v2',output.getvalue())

    def test_existing_destination_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'x.txt';src.write_bytes(b'ABC')
            dst=Path(tmp)/'x.dragon.txt';dst.write_bytes(b'original')
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    dc.main(['x','--file',str(src)])
            self.assertEqual(dst.read_bytes(),b'original')

    def test_unsupported_data_version_refused(self):
        with self.assertRaisesRegex(ValueError,'unsupported DATA'):
            dc.decode_data(b'DRAGON-CURVE-DATA/2 mode=byte passes=1 key=0\nabc')


if __name__=='__main__':
    unittest.main()
