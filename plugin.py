import fcntl
from pathlib import Path
from PyQt6.QtCore import QProcess
from playlite.providers import GenericPlugin
from playlite.lifecycle import show_warning
from .backup import LOCK
from .progress import ToolProgress

class Plugin(GenericPlugin):
    def before_launch(self, window, game):
        for dialog in window.findChildren(ToolProgress):
            if dialog.install_directory == game.get('InstallDirectory') and dialog.process.state() != QProcess.ProcessState.NotRunning:
                show_warning(window, 'Crack Tools', 'Wait for game-file processing to finish before launching.')
                return False
        lock = Path(game.get('InstallDirectory') or '') / LOCK
        if lock.is_file():
            if lock.is_symlink():
                return False
            with lock.open('a') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    show_warning(window, 'Crack Tools', 'Wait for game-file processing to finish before launching.')
                    return False
        return True
