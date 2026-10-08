"""Private subprocess entry point for Steam VM CLI jobs."""
import json
import os
from pathlib import Path
import re
import sys
import traceback
from playlite.storage import atomic_json

PRIVATE = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'playlite/steamautocrack'


def write_json(path, value):
    atomic_json(path, value)
    path.chmod(0o600)


def run(job, process):
    request = json.loads((job / 'request.json').read_text())
    # Credentials only live in memory during the request.
    public = dict(request)
    public.pop('ApiKey', None)
    write_json(job / 'request.json', public)
    def status(message):
        key = request.get('ApiKey', '')
        message = str(message).replace(key, '[redacted]') if key else str(message)
        log = job / 'log.txt'
        previous = log.read_text(errors='replace') if log.exists() else ''
        # Guest updates contain a rolling log; avoid repeating its whole contents.
        last = getattr(status, 'last', '')
        if message == last:
            return
        addition = message[len(last):] if last and message.startswith(last) else message + '\n'
        status.last = message
        log.write_text((previous + addition)[-262144:])
        (job / 'status.txt').write_text(message.splitlines()[-1] if message.splitlines() else '')
    return process(request, cancel=lambda: (job / 'cancel').exists(), status=status)


def main(process):
    os.umask(0o077)
    if len(sys.argv) != 2 or not re.fullmatch(r'[0-9a-f]{32}', sys.argv[1]):
        return 2
    job = PRIVATE / 'jobs' / sys.argv[1]
    if not job.is_dir():
        return 2
    try:
        result = run(job, process)
    except Exception as error:
        result = dict(Success=False, Message=str(error) if isinstance(error, ValueError) else 'Installation failed. See the log; backups were retained if recovery was needed.')
        with (job / 'log.txt').open('a') as log:
            log.write('\n' + re.sub(r'\b[a-fA-F0-9]{32}\b', '[redacted]', traceback.format_exc()))
    finally:
        (job / 'request.json').unlink(missing_ok=True)
    write_json(job / 'result.json', result)
    return 0 if result['Success'] else 1
