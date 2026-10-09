from pathlib import Path
import tempfile
import unittest
import errno
import hashlib
import io
from unittest.mock import Mock, patch
from source_support import ROOT
from cracktools_source.backup import BACKUP, digest, snapshot, finalize, restore, write_json, collect_cli_backups, protect_cli_backups, recover_cli_protection


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'steam_api64.dll').write_bytes(b'original')
        (self.root / 'assets.dat').write_bytes(b'immutable asset')

    def test_hash_reopens_stale_file_and_restarts_after_partial_read(self):
        stale = Mock()
        stale.__enter__ = Mock(return_value=stale)
        stale.__exit__ = Mock(return_value=False)
        stale.read.side_effect = [b'partial', OSError(errno.ESTALE, 'Stale file handle')]
        path = Mock()
        path.open.side_effect = [stale, io.BytesIO(b'complete')]
        with patch('cracktools_source.backup.time.sleep'):
            self.assertEqual(digest(path), hashlib.sha256(b'complete').hexdigest())
        self.assertEqual(path.open.call_count, 2)
        stale.__exit__.assert_called_once()

    def test_persistent_stale_handle_is_not_silenced(self):
        path = Mock()
        path.open.side_effect = OSError(errno.ESTALE, 'Stale file handle')
        with patch('cracktools_source.backup.time.sleep'):
            with self.assertRaises(OSError) as raised:
                digest(path)
        self.assertEqual(raised.exception.errno, errno.ESTALE)
        self.assertEqual(path.open.call_count, 6)

    def test_other_storage_errors_are_not_retried(self):
        path = Mock()
        path.open.side_effect = OSError(errno.EIO, 'I/O error')
        with patch('cracktools_source.backup.time.sleep') as sleep:
            with self.assertRaises(OSError):
                digest(path)
        path.open.assert_called_once()
        sleep.assert_not_called()

    def vanilla(self):
        return {'AppId': '123', 'BuildId': '1', 'Depots': {'1': '2'}, 'Files': {
            'steam_api64.dll': {'path': 'steam_api64.dll', 'sha1': hashlib.sha1(b'original').hexdigest(), 'size': 8},
            'game.exe': {'path': 'game.exe', 'sha1': hashlib.sha1(b'executable').hexdigest(), 'size': 10}}}

    def test_vanilla_backup_skips_unrelated_dlls_and_quarantines_old_generated_files(self):
        (self.root / 'game.exe').write_bytes(b'executable')
        (self.root / 'graphics.dll').write_bytes(b'large unrelated library')
        (self.root / 'steam_api64.dll.bak').write_bytes(b'old modified copy')
        (self.root / 'game.exe.unpacked.exe').write_bytes(b'old unpacked executable')
        settings = self.root / 'steam_settings'; settings.mkdir()
        (settings / 'configs.user.ini').write_text('old generated settings')
        snapshot(self.root, '123', vanilla=self.vanilla())
        manifest = __import__('json').loads((self.root / BACKUP / 'manifest.json').read_text())
        self.assertEqual({e['path'] for e in manifest['files']}, {'steam_api64.dll', 'game.exe'})
        self.assertFalse(settings.exists())
        self.assertFalse((self.root / 'steam_api64.dll.bak').exists())
        self.assertFalse((self.root / 'game.exe.unpacked.exe').exists())
        self.assertEqual(len(list((self.root / BACKUP / 'recovery').rglob('configs.user.ini'))), 1)
        (self.root / 'steam_api64.dll').write_bytes(b'modified')
        settings.mkdir(); (settings / 'configs.user.ini').write_text('new generated settings')
        finalize(self.root); restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')
        self.assertEqual((self.root / 'graphics.dll').read_bytes(), b'large unrelated library')
        self.assertFalse(settings.exists())
        self.assertFalse((self.root / 'steam_api64.dll.bak').exists())

    def test_modified_target_is_rejected_before_any_backup_or_quarantine(self):
        (self.root / 'game.exe').write_bytes(b'executable')
        (self.root / 'steam_api64.dll').write_bytes(b'not original')
        (self.root / 'steam_api64.dll.bak').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'Not a verified vanilla original'):
            snapshot(self.root, '123', vanilla=self.vanilla())
        self.assertFalse((self.root / BACKUP).exists())
        self.assertTrue((self.root / 'steam_api64.dll.bak').exists())

    def test_disabled_unpack_does_not_hash_or_copy_executables(self):
        (self.root / 'game.exe').write_bytes(b'modified executable')
        snapshot(self.root, '123', vanilla=self.vanilla(), unpack=False)
        manifest = __import__('json').loads((self.root / BACKUP / 'manifest.json').read_text())
        self.assertEqual([e['path'] for e in manifest['files']], ['steam_api64.dll'])

    def test_cli_cannot_reprocess_recovery_files_and_names_return_after_failure(self):
        snapshot(self.root, '123')
        recovery = self.root / BACKUP / 'recovery/old'
        recovery.mkdir(parents=True)
        dll = recovery / 'steam_api64.dll'; dll.write_bytes(b'preserved dll')
        exe = recovery / 'old.exe'; exe.write_bytes(b'preserved exe')
        with self.assertRaisesRegex(ValueError, 'CLI failed'):
            with protect_cli_backups(self.root):
                self.assertFalse(dll.exists()); self.assertFalse(exe.exists())
                self.assertFalse(list(recovery.rglob('*.exe')))
                raise ValueError('CLI failed')
        self.assertEqual(dll.read_bytes(), b'preserved dll')
        self.assertEqual(exe.read_bytes(), b'preserved exe')
        self.assertFalse((self.root / BACKUP / 'cli-protection.json').exists())

    def test_interrupted_cli_protection_journal_recovers_names(self):
        snapshot(self.root, '123')
        recovery = self.root / BACKUP / 'recovery'; recovery.mkdir()
        (recovery / 'protected-123').write_bytes(b'preserved')
        write_json(self.root / BACKUP / 'cli-protection.json',
                   [{'source': 'recovery/old.exe', 'hidden': 'recovery/protected-123'}])
        recover_cli_protection(self.root)
        self.assertEqual((recovery / 'old.exe').read_bytes(), b'preserved')

    def test_failed_manifest_save_puts_quarantined_files_back(self):
        (self.root / 'game.exe').write_bytes(b'executable')
        settings = self.root / 'steam_settings'; settings.mkdir()
        (settings / 'old.ini').write_text('previous settings')
        with patch('cracktools_source.backup.write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                snapshot(self.root, '123', vanilla=self.vanilla())
        self.assertEqual((settings / 'old.ini').read_text(), 'previous settings')
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')

    def test_missing_vanilla_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing vanilla target'):
            snapshot(self.root, '123', vanilla=self.vanilla())
        self.assertFalse((self.root / BACKUP).exists())

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
