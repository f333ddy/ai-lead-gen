"""Run logging and crash reporting for the scheduled daily run.

Task Scheduler launches python.exe with no console attached, which cost us two
whole runs' worth of digests before anyone could say why:

  * stdout falls back to the locale encoding (cp1252 on this box), so the bare
    `print(f"Analyzing {title}...")` in the eligibility loop raises
    UnicodeEncodeError the moment a headline carries a character cp1252 has no
    slot for -- a non-breaking hyphen, a narrow no-break space, a Polish
    l-slash, an emoji. That is why the run failed on some mornings and not
    others: it depended entirely on that day's headlines.

  * Nothing captures the output, so a crash leaves only exit code 1 in the
    event log and no traceback anywhere.

`start()` fixes both, and installs an excepthook so an unhandled exception is
written to the log and emailed out. Import this module and call `start()`
*before* the third-party and project imports: the excepthook is what turns a
venv that is missing a dependency -- a ModuleNotFoundError raised at import
time, which no try//except around main() can reach -- into an alert instead of
a silent no-digest morning.

Deliberately stdlib-only. A logger that needs a package installed to report
that a package is not installed would be useless exactly when it is needed.
"""
from __future__ import annotations

import atexit
import os
import smtplib
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"

# A month of mornings. Older runs have been superseded by later ones.
RETAIN_LOGS = 30

SMTP_HOST = "domain.lavi.com"
SMTP_PORT = 25
ALERT_FROM = "marketing@lavi.com"

_log_handle = None
_log_path: Path | None = None
_started = False


class _Tee:
    """Write to the console stream and the log file at once.

    `errors="replace"` on the console side is the point: the log file is UTF-8
    and lossless, but the inherited stream may still be a cp1252 handle we do
    not control, and a mangled character in a console nobody reads must never
    be what stops the run.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        self._handle.write(text)
        # Unbuffered on purpose -- a run that dies mid-scrape should still have
        # everything up to the failure on disk.
        self._handle.flush()
        try:
            self._stream.write(text)
            self._stream.flush()
        except Exception:
            pass
        return len(text)

    def flush(self) -> None:
        for target in (self._handle, self._stream):
            try:
                target.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return self._stream.isatty()
        except Exception:
            return False


def _alert_recipients() -> list[str]:
    """Who hears about a crash.

    Falls back to QA_EMAIL_RECIPIENTS so the alert list tracks the people
    already receiving the QA digest without a second variable to maintain.
    """
    raw = (
        os.environ.get("ALERT_EMAIL_RECIPIENTS")
        or os.environ.get("QA_EMAIL_RECIPIENTS")
        or "federico.aguilar@lavi.com"
    )
    return [address.strip() for address in raw.split(",") if address.strip()]


def _prune_old_logs() -> None:
    logs = sorted(
        LOG_DIR.glob("run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in logs[RETAIN_LOGS:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _send_alert(subject: str, body: str) -> None:
    recipients = _alert_recipients()
    if not recipients:
        return
    message = EmailMessage()
    message["From"] = ALERT_FROM
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.send_message(message)
    except Exception as exc:
        # Nothing left to escalate to -- record it and let the exit code speak.
        print(f"[runlog] could not send alert email: {exc}")


def _log_tail(limit: int = 60) -> str:
    if _log_path is None or not _log_path.exists():
        return "(no log file)"
    try:
        lines = _log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read log: {exc})"
    return "\n".join(lines[-limit:])


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("\n=== UNHANDLED EXCEPTION -- run aborted ===")
    print(text)
    sys.stdout.flush()
    _send_alert(
        f"[ALERT] AI lead-gen run FAILED: {exc_type.__name__}",
        "The daily lead-gen run aborted with an unhandled exception.\n\n"
        f"Log file: {_log_path}\n\n"
        f"--- traceback ---\n{text}\n"
        f"--- last 60 log lines ---\n{_log_tail()}\n",
    )


def start() -> Path:
    """Begin capturing this run. Returns the log file path."""
    global _log_handle, _log_path, _started
    if _started:
        return _log_path  # type: ignore[return-value]
    _started = True

    # Do this first and unconditionally: every print below depends on it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # No console at all (pythonw) leaves these as None -- the tee below
            # still writes the log, which is the part that matters.
            pass

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_path = LOG_DIR / f"run-{datetime.now():%Y-%m-%d_%H%M%S}.log"
    _log_handle = open(_log_path, "w", encoding="utf-8", errors="replace")

    sys.stdout = _Tee(sys.stdout, _log_handle)
    sys.stderr = _Tee(sys.stderr, _log_handle)
    sys.excepthook = _excepthook
    atexit.register(_close)

    _prune_old_logs()
    print(f"=== run started {datetime.now():%Y-%m-%d %H:%M:%S} -> {_log_path.name} ===")
    return _log_path


def _close() -> None:
    print(f"=== run finished {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    if _log_handle is not None:
        try:
            _log_handle.flush()
            _log_handle.close()
        except OSError:
            pass
