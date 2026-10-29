"""Bounded, asynchronous query of an engine's compiled optional components."""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal


class EngineCapabilities(QObject):
    changed = Signal()
    TIMEOUT_MS = 2000
    MAX_BYTES = 4096

    def __init__(self, parent=None):
        super().__init__(parent)
        self._identity = None
        self._status = "unverified"
        self._process = None
        self._retired_processes = []

    @staticmethod
    def decode(payload: bytes):
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            return None
        if (not isinstance(data, dict) or data.get("type") != "capabilities"
                or type(data.get("protocol_version")) is not int or data["protocol_version"] != 1
                or type(data.get("cuda_preprocess")) is not bool):
            return None
        return data["cuda_preprocess"]

    def request(self, executable: Path | None, environment: QProcessEnvironment) -> str:
        try:
            path = executable.resolve(strict=True)
            info = path.stat()
            identity = (str(path), info.st_size, info.st_mtime_ns)
        except (AttributeError, OSError):
            self.stop()
            return "unverified"
        if identity == self._identity:
            return self._status
        self.stop()
        self._identity, self._status = identity, "checking"
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(path.parent))
        timer = QTimer(process)
        timer.setSingleShot(True)
        stdout = bytearray()
        total = 0

        def complete(result=None):
            timer.stop()
            if self._process is not process:
                return
            self._process = None
            if process.state() != QProcess.ProcessState.NotRunning:
                if process not in self._retired_processes:
                    self._retired_processes.append(process)
                process.kill()
            self._status = "available" if result is True else "unavailable" if result is False else "unverified"
            self.changed.emit()

        def read(channel):
            nonlocal total
            if self._process is not process:
                return
            process.setReadChannel(channel)
            chunk = bytes(process.read(self.MAX_BYTES - total + 1))
            total += len(chunk)
            if total > self.MAX_BYTES:
                complete()
            elif channel == QProcess.ProcessChannel.StandardOutput:
                stdout.extend(chunk)

        def finished(code, status):
            read(QProcess.ProcessChannel.StandardOutput)
            read(QProcess.ProcessChannel.StandardError)
            valid = code == 0 and status == QProcess.ExitStatus.NormalExit
            complete(self.decode(bytes(stdout)) if valid else None)
            if process in self._retired_processes:
                self._retired_processes.remove(process)
            process.deleteLater()

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                complete()
                if process in self._retired_processes:
                    self._retired_processes.remove(process)
                process.deleteLater()

        process.readyReadStandardOutput.connect(lambda: read(QProcess.ProcessChannel.StandardOutput))
        process.readyReadStandardError.connect(lambda: read(QProcess.ProcessChannel.StandardError))
        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        timer.timeout.connect(complete)
        timer.start(self.TIMEOUT_MS)
        process.start(str(path), ["--headless", "--capabilities"])
        return self._status

    def stop(self):
        process, self._process = self._process, None
        self._identity, self._status = None, "unverified"
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning and process not in self._retired_processes:
                self._retired_processes.append(process)
            process.kill()

    def cleanup_complete(self):
        return self._process is None and all(process.state() == QProcess.ProcessState.NotRunning
                                            for process in self._retired_processes)
