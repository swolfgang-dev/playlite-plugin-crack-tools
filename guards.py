"""Game locks and shared local recovery, independent of either installer."""
import fcntl
from pathlib import Path
from .backup import LOCK, restore


def ensure_stopped(window, game):
    if window.game_detection.status(game['Id']) in ('Launching', 'Running'):
        raise ValueError('Stop the game before modifying or restoring its files.')
    from playlite.providers import IntegrationPlugin
    for provider in window.game_providers:
        if isinstance(provider, IntegrationPlugin) and provider.owns(game) and game['Id'] in provider.detect_running([game]):
            raise ValueError('Stop the game before modifying or restoring its files.')


def restore_game(request, cancel=lambda: False, status=lambda text: None):
    root = Path(request['InstallDirectory']).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('Choose an installed game folder.')
    lock = root / LOCK
    if lock.is_symlink():
        raise ValueError('The game lock cannot be a symlink.')
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another operation is already modifying this game.') from None
        def check():
            if cancel():
                raise ValueError('Restoration cancelled before changing game files.')
        restore(root.resolve(), check, status)
    return dict(Success=True, Message='Original game files restored.')
