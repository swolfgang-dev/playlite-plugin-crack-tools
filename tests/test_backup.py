from pathlib import Path
import tempfile
import unittest
from source_support import ROOT
from cracktools_source.backup import BACKUP, digest, snapshot, finalize, restore, write_json, collect_cli_backups


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'steam_api64.dll').write_bytes(b'original')
        (self.root / 'assets.dat').write_bytes(b'immutable asset')

    def test_cli_changes_and_new_files_restore_with_existing_settings_intact(self):
        settings = self.root / 'steam_settings'
        settings.mkdir()
        (settings / 'save.dat').write_bytes(b'original save')
        snapshot(self.root, '123')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        (self.root / 'steam_api64.dll.bak').write_bytes(b'original')
        (settings / 'new.ini').write_text('generated')
        (settings / 'save.dat').write_bytes(b'changed')
        finalize(self.root)
        (self.root / 'new-assets.dat').write_bytes(b'unrelated new asset')
        restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')
        self.assertEqual((settings / 'save.dat').read_bytes(), b'original save')
        self.assertFalse((settings / 'new.ini').exists())
        self.assertFalse((self.root / 'steam_api64.dll.bak').exists())
        self.assertEqual((self.root / 'assets.dat').read_bytes(), b'immutable asset')
        self.assertEqual((self.root / 'new-assets.dat').read_bytes(), b'unrelated new asset')
        self.assertFalse((self.root / BACKUP).exists())

    def test_changed_game_file_is_preserved_before_restore(self):
        snapshot(self.root, '123')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        finalize(self.root)
        (self.root / 'steam_api64.dll').write_bytes(b'updated game')
        restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')
        saved = list((self.root / BACKUP / 'recovery').glob('*/steam_api64.dll'))
        self.assertEqual(saved[0].read_bytes(), b'updated game')
        self.assertFalse((self.root / BACKUP / 'manifest.json').exists())
        snapshot(self.root, '123')
        self.assertEqual(saved[0].read_bytes(), b'updated game')

    def test_existing_and_generated_cli_backups_are_collected_and_rollback_is_exact(self):
        bak = self.root / 'steam_api64.dll.bak'
        bak.write_bytes(b'older original')
        snapshot(self.root, '123')
        collect_cli_backups(self.root, 'existing')
        self.assertFalse(bak.exists())
        bak.write_bytes(b'original')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        collect_cli_backups(self.root, 'generated')
        self.assertFalse(bak.exists())
        self.assertEqual((self.root / BACKUP / 'cli/generated/steam_api64.dll.bak').read_bytes(), b'original')
        finalize(self.root)
        restore(self.root)
        self.assertEqual(bak.read_bytes(), b'older original')
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')

    def test_legacy_manifest_still_restores(self):
        saved = self.root / BACKUP
        saved.mkdir()
        (saved / 'original-0').write_bytes(b'original')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        write_json(saved / 'manifest.json', {'files': [dict(path='steam_api64.dll', backup='original-0',
            original=digest(saved / 'original-0'), applied=digest(self.root / 'steam_api64.dll'))]})
        restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')

    def test_symlink_game_content_is_rejected_before_backup_or_cli(self):
        (self.root / 'linked.dll').symlink_to(self.root / 'steam_api64.dll')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            snapshot(self.root, '123')
        self.assertFalse((self.root / BACKUP).exists())

    def test_backup_traversal_is_rejected(self):
        saved = self.root / BACKUP
        saved.mkdir()
        write_json(saved / 'manifest.json', {'files': [dict(path='../outside', applied='bad')]})
        with self.assertRaisesRegex(ValueError, 'Invalid backup path'):
            restore(self.root)

    def test_tracks_new_files_and_empty_directories_but_preserves_later_user_changes(self):
        (self.root / 'untouched.dll').write_bytes(b'old library')
        (self.root / 'original-empty').mkdir()
        snapshot(self.root, '123')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        (self.root / 'generated/nested').mkdir(parents=True)
        (self.root / 'generated/settings.json').write_text('generated')
        (self.root / 'original-empty').rmdir()
        finalize(self.root)
        (self.root / 'untouched.dll').write_bytes(b'user updated library')
        (self.root / 'user.dat').write_bytes(b'user data')
        restore(self.root)
        self.assertEqual((self.root / 'untouched.dll').read_bytes(), b'user updated library')
        self.assertEqual((self.root / 'user.dat').read_bytes(), b'user data')
        self.assertFalse((self.root / 'generated').exists())
        self.assertTrue((self.root / 'original-empty').is_dir())

    def test_new_user_file_keeps_generated_directory(self):
        snapshot(self.root, '123')
        (self.root / 'steam_settings').mkdir()
        (self.root / 'steam_settings/generated.txt').write_text('generated')
        finalize(self.root)
        (self.root / 'steam_settings/user.txt').write_text('user')
        restore(self.root)
        self.assertFalse((self.root / 'steam_settings/generated.txt').exists())
        self.assertEqual((self.root / 'steam_settings/user.txt').read_text(), 'user')
