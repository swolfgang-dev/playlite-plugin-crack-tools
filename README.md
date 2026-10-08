# Crack Tools for Playlite

Shared backup, restore, progress, private job logs and game locks for independent
installers. SteamAutoCrack and GameCopyWorld each depend on this plugin.
Crack Tools has no dependency on an installer, Steam Downloader, Wine or a VM.

Existing `.playlite-steamautocrack/` backups and job folders retain their paths
for compatibility. Both installers use the same manifest, so each can restore
the previous operation before installing new files. Changed files are preserved
under `recovery/`; unrelated later additions are kept. Only plugin-created empty
directories are removed.

Build: `python3 tools/build_release.py`. Install `dist/plugin.zip` before the
installer updates. Tests: `python3 -m unittest discover -s tests -q`.
