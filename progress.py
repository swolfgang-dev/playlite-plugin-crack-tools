import json
import os
import sys
import uuid
from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QApplication

class ToolProgress(QDialog):
    plugin_id = 'CrackTools'
    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.setProperty('playliteBackgroundJob', True)
        self.setWindowTitle(self.plugin_id)
        self.install_directory = request.get('InstallDirectory')
        self.resize(800, 220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        self.status = QLabel('Starting installation…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.activity_log = QPlainTextEdit(self)
        self.activity_log.setReadOnly(True)
        self.activity_log.setPlaceholderText('VM and restoration log will appear here…')
        self.details_toggle = QPushButton('Show details')
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self.activity_log.setVisible)
        self.details_toggle.toggled.connect(lambda checked: self.details_toggle.setText('Hide details' if checked else 'Show details'))
        layout.addWidget(self.details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.activity_log.hide()
        layout.addWidget(self.activity_log, 1)
        copy = QPushButton('Copy log')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.activity_log.toPlainText()))
        self.log_path = QLabel()
        self.log_path.setWordWrap(True)
        self.log_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.log_path.hide()
        self.details_toggle.toggled.connect(self.log_path.setVisible)
        self.details_toggle.toggled.connect(lambda expanded: self.resize(self.width(), min(680,self.screen().availableGeometry().height()-48) if expanded else (420 if hasattr(self,'log') else 220)))
        layout.addWidget(self.log_path)
        self.button = QPushButton('Cancel')
        self.button.clicked.connect(self.cancel_or_close)
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addWidget(copy)
        buttons.addStretch()
        buttons.addWidget(self.button)
        layout.addLayout(buttons)
        self.process = QProcess(self)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.start_job(request)

    def start_job(self, request):
        from .jobs import PRIVATE, write_json
        self.install_directory = request.get('InstallDirectory')
        self.job = PRIVATE / 'jobs' / uuid.uuid4().hex
        self.job.mkdir(parents=True, mode=0o700)
        self.activity_log.clear()
        self.log_path.setText('Log: ' + str(self.job / 'log.txt'))
        os.chmod(self.job, 0o700)
        write_json(self.job / 'request.json', request)
        self.button.setText('Cancel')
        self.details_toggle.setChecked(False)
        self.timer.start(500)
        self.process.start(sys.executable, ['-m', 'playlite.plugin_runner', self.plugin_id, self.job.name])

    def poll(self):
        log = self.job / 'log.txt'
        if log.exists():
            text = log.read_text(errors='replace')[-262144:]
            if text != self.activity_log.toPlainText():
                bar = self.activity_log.verticalScrollBar()
                position = bar.value()
                following = position >= bar.maximum() - 2
                self.activity_log.setPlainText(text)
                bar.setValue(bar.maximum() if following else position)
        status = self.job / 'status.txt'
        if status.exists():
            self.status.setText(status.read_text(errors='replace')[-500:])

    def failed(self, error):
        self.timer.stop()
        (self.job / 'request.json').unlink(missing_ok=True)
        self.status.setText('Could not start installation. See the log for details.')
        self.activity_log.appendPlainText(self.process.errorString())
        self.button.setText('Close')

    def process_finished(self, *args):
        self.timer.stop()
        self.poll()
        result = self.job / 'result.json'
        try:
            self.result_data = json.loads(result.read_text())
        except (OSError, ValueError):
            self.result_data = dict(Success=False, Message='Installation stopped. Backups remain available for recovery.')
        self.status.setText(self.result_data.get('Message', 'Finished'))
        message = self.result_data.get('Message', 'Finished')
        if not self.result_data.get('Success'):
            self.details_toggle.setChecked(True)
        lines = self.activity_log.toPlainText().rstrip().splitlines()
        if not lines or lines[-1] != message:
            self.activity_log.appendPlainText(message)
        self.button.setText('Close')

    def cancel_or_close(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            (self.job / 'cancel').touch()
            self.status.setText('Cancelling… Restoring original game files.')
        else:
            self.accept()

    def reject(self):
        self.cancel_or_close()


class ToolBatchProgress(ToolProgress):
    """One progress dialog processes a selection sequentially."""
    def __init__(self, jobs, parent=None, preflight=lambda game: None):
        self.jobs = jobs
        self.index = 0
        self.results = []
        self.cancelled = False
        self.batch_finished = False
        self.preflight = preflight
        super().__init__(jobs[0][1], parent)
        self.resize(800, 420)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.layout().insertWidget(1, self.log)
        self.status.setText(f'1/{len(jobs)}: {jobs[0][0]["Name"]}')

    def process_finished(self, *args):
        super().process_finished(*args)
        game = self.jobs[self.index][0]
        self.results.append((game['Name'], self.result_data))
        self.log.appendPlainText(game['Name'] + ': ' + self.result_data.get('Message', 'Finished'))
        self.index += 1
        QTimer.singleShot(0, self.advance)

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            super().failed(error)
            self.process_finished()

    def advance(self):
        while self.index < len(self.jobs) and not self.cancelled:
            game, request = self.jobs[self.index]
            try:
                self.preflight(game)
            except (ValueError, OSError) as error:
                result = dict(Success=False, Message=str(error))
                self.results.append((game['Name'], result))
                self.log.appendPlainText(game['Name'] + ': ' + str(error))
                self.index += 1
                continue
            self.start_job(request)
            self.status.setText(f'{self.index + 1}/{len(self.jobs)}: {game["Name"]}')
            return
        self.batch_finished = True
        succeeded = sum(bool(result.get('Success')) for _, result in self.results)
        self.status.setText(f'Finished: {succeeded} succeeded, {len(self.results) - succeeded} failed, {len(self.jobs) - self.index} skipped.')
        self.button.setText('Close')

    def cancel_or_close(self):
        if self.batch_finished:
            self.accept()
            return
        self.cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            super().cancel_or_close()
        else:
            self.advance()
