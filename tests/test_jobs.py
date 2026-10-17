import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from source_support import ROOT
from cracktools_source import jobs


class JobPrivacyTests(unittest.TestCase):
    def test_failure_result_and_log_do_not_expose_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            identity = 'b' * 32
            job = private / 'jobs' / identity
            job.mkdir(parents=True)
            secret = 'a' * 32
            (job / 'request.json').write_text(json.dumps({'ApiKey': secret}))
            def fail(request, cancel, status):
                status('Processing with ' + request['ApiKey'])
                raise ValueError('Rejected ' + request['ApiKey'])
            with patch.object(jobs, 'PRIVATE', private), patch('sys.argv', ['worker', identity]):
                self.assertEqual(jobs.main(fail), 1)
            self.assertFalse((job / 'request.json').exists())
            for name in ('log.txt', 'status.txt', 'result.json'):
                self.assertNotIn(secret, (job / name).read_text())
            self.assertIn('[redacted]', json.loads((job / 'result.json').read_text())['Message'])
