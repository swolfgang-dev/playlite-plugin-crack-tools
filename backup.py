"""Verified original-file recovery, shared by legacy and VM CLI jobs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

BACKUP = '.playlite-steamautocrack'
LOCK = '.playlite-steamautocrack.lock'


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
    temporary.chmod(0o600)
    temporary.replace(path)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def checked_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Invalid backup path.')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Backup paths must remain inside the game folder.')
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError('Backup paths cannot contain symlinks.')
    return path


def files(root):
    output = {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != BACKUP]
        for name in dirs + names:
            if (Path(folder) / name).is_symlink():
                raise ValueError('Game folders containing symlinks must be reviewed before CLI processing.')
        for name in names:
            if name == LOCK:
                continue
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError('Game folders must contain only regular files and directories.')
            output[str(path.relative_to(root))] = path
    return output


def directories(root):
    result = []
    for folder, dirs, _ in os.walk(root, followlinks=False):
        if Path(folder) == root:
            dirs[:] = [name for name in dirs if name != BACKUP]
        for name in dirs:
            path = Path(folder) / name
            checked_path(root, path.relative_to(root))
            result.append(path.relative_to(root).as_posix())
    return result


def mutable_file(relative, path):
    return (path.suffix.lower() in ('.dll', '.exe', '.so', '.bak', '.unpacked') or
            'steam_settings' in Path(relative).parts or
            path.name.lower() in ('steam_interfaces.txt', 'local_save.txt', 'steamapicheckbypass.json'))


def snapshot(root, appid, check=lambda: None, status=lambda text: None):
    backup = root / BACKUP
    if backup.is_symlink() or (backup.exists() and any(p.name != 'recovery' for p in backup.iterdir())):
        raise ValueError('Restore the previous original-file backup before processing again.')
    existing = files(root)
    if not any(path.name.lower() in ('steam_api.dll', 'steam_api64.dll') for path in existing.values()):
        raise ValueError('No Windows Steam API DLL found in the shared game folder.')
    original_directories = directories(root)
    backup.mkdir(mode=0o700, exist_ok=True)
    entries = []
    try:
        for relative, path in existing.items():
            check()
            if not mutable_file(relative, path):
                continue
            status('Backing up ' + relative + '…')
            saved = 'original-' + str(len(entries))
            original = digest(path)
            shutil.copy2(path, backup / saved)
            if digest(backup / saved) != original or digest(path) != original:
                raise ValueError('Game files changed while preparing their backup.')
            entries.append(dict(path=relative, backup=saved, original=original, applied=original))
        write_json(backup / 'manifest.json', dict(AppId=appid, files=entries,
                   FileSet=list(existing), OriginalDirectories=original_directories, Backend='vm-cli', Status='Applying'))
    except Exception:
        clear_backup(backup)
        raise


def clear_backup(backup):
    for path in backup.iterdir():
        if path.name == 'recovery':
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    if not any(backup.iterdir()):
        backup.rmdir()


def cli_backup(path):
    return path.name.lower().endswith(('.dll.bak', '.exe.bak'))


def collect_cli_backups(root, category, check=lambda: None, status=lambda text: None):
    backup = root / BACKUP
    for relative, path in files(root).items():
        if not cli_backup(path):
            continue
        check()
        target = checked_path(backup, 'cli/' + category + '/' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError('CLI backup destination already exists: ' + relative)
        status('Moving CLI backup into managed folder: ' + relative)
        path.rename(target)


def collect_existing_settings(root, check=lambda: None, status=lambda text: None):
    directories = {path.parent / 'steam_settings' for path in files(root).values()
                   if path.name.lower() in ('steam_api.dll', 'steam_api64.dll')}
    for directory in directories:
        if not directory.exists():
            continue
        check()
        relative = directory.relative_to(root).as_posix()
        target = checked_path(root / BACKUP, 'cli/existing-settings/' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        status('Preserving previous settings: ' + relative)
        directory.rename(target)


def finalize(root):
    backup = root / BACKUP
    manifest = json.loads((backup / 'manifest.json').read_text())
    current = files(root)
    entries = {}
    for entry in manifest['files']:
        path = checked_path(root, entry['path'])
        entry['applied'] = digest(path) if path.is_file() else None
        # Restore only files actually touched during processing. A later user
        # update to an untouched library must not be replaced with our copy.
        if not entry.get('backup') or entry['applied'] != entry.get('original'):
            entries[entry['path']] = entry
    for relative in current.keys() - set(manifest['FileSet']):
        entries[relative] = dict(path=relative, applied=digest(current[relative]))
    manifest['files'] = list(entries.values())
    if 'OriginalDirectories' in manifest:
        manifest['CreatedDirectories'] = sorted(set(directories(root)) - set(manifest['OriginalDirectories']))
    manifest['Status'] = 'Complete'
    write_json(backup / 'manifest.json', manifest)


def restore(root, check=lambda: None, status=lambda text: None):
    backup = root / BACKUP
    if backup.is_symlink():
        raise ValueError('Backup directory cannot be a symlink.')
    manifest = json.loads((backup / 'manifest.json').read_text())
    entries = manifest['files']
    # Validate every entry before changing any game file. This also supports
    # the manifests produced by the former native engine.
    original_dirs = manifest.get('OriginalDirectories', [])
    created_dirs = manifest.get('CreatedDirectories', [])
    for relative in original_dirs + created_dirs:
        checked_path(root, relative)
    conflicts = []
    for entry in entries:
        check()
        path = checked_path(root, entry['path'])
        if path.exists() and digest(path) not in (entry.get('original'), entry.get('applied')):
            conflicts.append((entry['path'], path, digest(path)))
        if entry.get('backup'):
            original = checked_path(backup, entry['backup'])
            if not original.is_file() or digest(original) != entry['original']:
                raise ValueError('Original backup verification failed. Nothing was restored.')
    if conflicts:
        recovery = backup / 'recovery' / uuid.uuid4().hex
        recovery.mkdir(parents=True, mode=0o700)
        for relative, path, expected in conflicts:
            check()
            target = checked_path(recovery, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if digest(target) != expected or digest(path) != expected:
                raise ValueError('Changed-file recovery verification failed; originals were not restored.')
        status('Preserved changed files in ' + str(recovery))
    for entry in entries:
        path = checked_path(root, entry['path'])
        if entry.get('backup'):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.playlite-restore-tmp')
            try:
                shutil.copy2(backup / entry['backup'], temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
    for relative in original_dirs:
        checked_path(root, relative).mkdir(parents=True, exist_ok=True)
    for relative in sorted(created_dirs, key=lambda value: len(Path(value).parts), reverse=True):
        directory = checked_path(root, relative)
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                # Later user files keep their containing directory alive.
                pass
    clear_backup(backup)
    status('Original game files restored.')
