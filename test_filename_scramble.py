"""Filename obfuscation tests, including full file workflows."""
import io
import base64
import hashlib
import zipfile
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import dragon_curve as dc

class FileNameTests(unittest.TestCase):
    def test_metadata_roundtrip_and_wrong_key(self):
        for name in ('report.zip', 'notes.txt', '🐉-café.txt', '.hidden', 'a', 'a'*236+'.zip'):
            for key in ('', 'Dragon_Curve', '82e7-1555-0dC9-fc11'):
                with self.subTest(name=name, key=key):
                    token=dc.scramble_filename(name,key)
                    self.assertEqual(dc.restore_filename(token,key),name)
                    if key:
                        with self.assertRaisesRegex(ValueError,'wrong key'):
                            dc.restore_filename(token,'wrong-key')
                    self.assertRegex(dc.scrambled_path(Path(name),token).name, r"^dc-[0-9a-f]{24}\.dragon$")
    def test_reject_unsafe_names_and_tampering(self):
        for name in ('../evil','nested/file','folder\\file','.', '..', '', 'a\0b', 'a'*256):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dc.scramble_filename(name,'key')
        tok=dc.scramble_filename('a.zip','k')
        for invalid in (tok+'x', tok[:-1]+'A'*50, '/etc/passwd','', '%%%'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                dc.restore_filename(invalid,'k')
    def test_binary_all_units_with_name_metadata(self):
        for unit in ('byte','hex','bit','character'):
            key='Dragon_Curve'
            name='Archive 🐉.zip'
            data='some café 🐉'.encode() if unit=='character' else bytes(range(256))
            token=dc.scramble_filename(name,key)
            package=dc.encode_data(data,key,unit,7,name_token=token)
            self.assertEqual(dc.decode_data(package,key),data)
            self.assertEqual(dc.restore_filename(dc._data_header_info(package)[3],key),name)
            self.assertEqual(dc._data_header_info(package)[0],unit)
            self.assertEqual(dc._data_header_info(package)[1],7)
    def test_binary_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            src=root/'Puzzle File.zip'
            payload=b'PK\x03\x04'+bytes(range(40))
            src.write_bytes(payload)
            args=['x','--unit','hex','--passes','7','--key','Dragon_Curve','--scramble-name','--file',str(src)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(args),0)
            token=dc.scramble_filename(src.name,'Dragon_Curve')
            encoded=dc.scrambled_path(src,token)
            self.assertTrue(encoded.exists())
            self.assertTrue(encoded.name.startswith('dc-'))
            # Rename original to avoid overwrite; decryption should restore original name.
            src.unlink()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','byte','--decrypt','--key','Dragon_Curve','--file',str(encoded)]),0)
            self.assertEqual(src.read_bytes(),payload)
            self.assertTrue(encoded.exists())
    def test_existing_destination_does_not_overwrite_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'x.zip';source.write_bytes(b'abc')
            token=dc.scramble_filename(source.name,'abc')
            encoded=dc.scrambled_path(source,token)
            encoded.write_bytes(b'untouchable')
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    dc.main(['x','--unit','byte','--key','abc','--scramble-name','--file',str(source)])
            self.assertEqual(encoded.read_bytes(),b'untouchable')
    def test_binary_no_header_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'x'; source.write_bytes(b'abc')
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    dc.main(['x','--unit','byte','--no-header','--scramble-name','--file',str(source)])
    def test_default_file_cli_roundtrip_is_data_3(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            src=root/'words café.txt'; plain='Hello\nDragon Curve! 🐉'
            src.write_text(plain)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--scramble-name','--key','paper',
                                          '--unit','character','--file',str(src)]),0)
            tok=dc.scramble_filename(src.name,'paper')
            encoded=dc.scrambled_path(src,tok)
            self.assertTrue(encoded.read_bytes().startswith(b'DRAGON-CURVE-DATA/3 mode=character'))
            src.unlink()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--decrypt','--key','paper','--file',str(encoded)]),0)
            self.assertEqual(src.read_text(),plain)

    def test_legacy_header_rejection(self):
        for version in (1,2):
            with self.subTest(version=version),self.assertRaisesRegex(ValueError,'retired'):
                dc.decode_data(f'DRAGON-CURVE-TEXT/{version} key=0\nolleH'.encode())

    def test_cli_custom_output_overrides_stored_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);src=root/'filename.jpg';src.write_bytes(b'123\x00\xff')
            output=root/'special.bin'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','byte','--scramble-name','--file',str(src),'--output',str(output)]),0)
            final=root/'manual.dat'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','byte','--decrypt','--file',str(output),'--output',str(final)]),0)
            self.assertEqual(final.read_bytes(),src.read_bytes())

    def test_menu_binary_scrambled_zip_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            src=root/'Enigma archive.zip'
            with zipfile.ZipFile(src,'w') as archive:
                archive.writestr('cipher.txt','ABCD EFGH')
                archive.writestr('readme.txt','Example text')
            original=src.read_bytes()
            # Prompts: source, unit, passes, key, scramble?, destination.
            replies=iter([str(src),'2','7','Dragon_Curve','y',''])
            with patch('builtins.input',side_effect=lambda _prompt: next(replies)):
                with redirect_stdout(io.StringIO()):
                    dc._menu_binary_file(False)
            token=dc.scramble_filename(src.name,'Dragon_Curve')
            scrambled=dc.scrambled_path(src,token)
            self.assertTrue(scrambled.exists())
            self.assertFalse(zipfile.is_zipfile(scrambled))
            src.unlink()
            # Prompts on decrypt: input, key, destination. Unit & passes detected.
            replies=iter([str(scrambled),'Dragon_Curve',''])
            with patch('builtins.input',side_effect=lambda _prompt: next(replies)):
                with redirect_stdout(io.StringIO()):
                    dc._menu_binary_file(True)
            self.assertEqual(src.read_bytes(),original)
            with zipfile.ZipFile(src) as archive:
                self.assertEqual(archive.read('cipher.txt'),b'ABCD EFGH')

    def test_wrong_key_with_manual_output_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            src=root/'secret.dat'; src.write_bytes(b'plaintext')
            dest=root/'encoded'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','hex','--key','correct','--scramble-name','--file',str(src),'--output',str(dest)]),0)
            attempt=root/'wrong-key-out.dat'
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    dc.main(['x','--unit','byte','--decrypt','--key','incorrect','--file',str(dest),'--output',str(attempt)])
            self.assertFalse(attempt.exists())

    def test_crafted_filename_with_path_separator_is_rejected(self):
        name=b'../evil.txt'
        packet=dc._FILENAME_MAGIC+name+hashlib.sha256(name).digest()[:8]
        body=dc.transform_units(packet,dc._filename_key('k'),'byte',7)
        token=base64.urlsafe_b64encode(body).decode().rstrip('=')
        with self.assertRaisesRegex(ValueError,'invalid original filename'):
            dc.restore_filename(token,'k')

    def test_filename_metadata_in_character_mode_map(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'map.txt'; src.write_text('Plain text with turns 🐉')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','character','--passes','7','--key','k',
                                      '--scramble-name','--map','--map-pass','7','--file',str(src)]),0)
            dst=dc.scrambled_path(src,dc.scramble_filename(src.name,'k'))
            self.assertTrue(dst.exists())
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(dc.main(['x','--unit','character','--decrypt','--map',
                    '--key','k','--file',str(dst),'--output',str(src.parent/'out.txt')]),0)
            self.assertEqual((src.parent/'out.txt').read_bytes(),src.read_bytes())
    def test_data3_default_header_is_unchanged(self):
        self.assertEqual(dc.encode_data(b'abc',unit='byte').partition(b'\n')[0],
                         b'DRAGON-CURVE-DATA/3 mode=byte passes=1 key=0')

if __name__ == '__main__': unittest.main()
