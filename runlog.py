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
_crashed = False

# Populated by record() during the run; drained into a summary email at exit.
# Kept separate from the crash alert: these are failures the caller already
# handled and moved past, not the reason the process is dying.
_summary: dict[str, list[str]] = {}

_CATEGORY_LABELS = {
    "raw_document_failures": "Raw documents that failed to persist",
    "gate_failures": "Eligibility gate calls that failed",
    "persistence_failures": "Enriched documents that failed to persist",
    "scraper_failures": "Scrapers that failed entirely",
    "hubspot_failures": "HubSpot API calls that failed",
    "team_email_skips": "Digests that did not send",
    "article_fetch_failures": "Individual articles that failed to fetch",
}

# Per category, in the summary email. The full list is always in the log.
_MAX_ITEMS_SHOWN = 20


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


def record(category: str, detail: str) -> None:
    """Note a handled failure for the end-of-run summary email.

    Doesn't print -- call sites already print their own message; this only
    adds it to the tally so isolated, individually-harmless failures (one
    article, one team, one HubSpot call) are still visible in aggregate
    instead of scrolling past in a log nobody opens.
    """
    _summary.setdefault(category, []).append(detail)


def _build_summary_email() -> tuple[str, str] | None:
    if not _summary:
        return None
    total = sum(len(items) for items in _summary.values())
    categories = len(_summary)
    subject = (
        f"[Run Summary] {total} issue(s) across {categories} "
        f"categor{'y' if categories == 1 else 'ies'}"
    )
    lines = [
        "The daily lead-gen run completed without crashing, but the following",
        "were caught, logged, and skipped along the way.",
        "",
        f"Log file: {_log_path}",
        "",
    ]
    for category, items in _summary.items():
        label = _CATEGORY_LABELS.get(category, category)
        lines.append(f"--- {label} ({len(items)}) ---")
        for item in items[:_MAX_ITEMS_SHOWN]:
            lines.append(f"  - {item}")
        if len(items) > _MAX_ITEMS_SHOWN:
            lines.append(f"  ... and {len(items) - _MAX_ITEMS_SHOWN} more (see log)")
        lines.append("")
    return subject, "\n".join(lines)


def _log_tail(limit: int = 60) -> str:
    if _log_path is None or not _log_path.exists():
        return "(no log file)"
    try:
        lines = _log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read log: {exc})"
    return "\n".join(lines[-limit:])


def fatal(message: str) -> None:
    """Abort the run immediately, with its own dedicated alert email.

    For preconditions that make every subsequent minute of work worthless --
    e.g. the HubSpot industries lookup came back empty, so every gate call
    today would classify against nothing. Raising here as a normal exception
    would either route through the generic crash alert (unclear about *why*
    the run is stopping) or, for SystemExit specifically, through neither
    alert path at all: the interpreter hands SystemExit straight to process
    exit without ever calling sys.excepthook (verified empirically -- it is
    not a documented guarantee). So this sends its own clear alert before
    exiting, and marks the run "crashed" to suppress the end-of-run summary
    email in _close(), which would otherwise fire right after and duplicate
    the notification.
    """
    global _crashed
    _crashed = True
    print(f"\n=== FATAL -- aborting run: {message} ===")
    sys.stdout.flush()
    _send_alert(
        "[FATAL] AI lead-gen run aborted before finishing its work",
        "The daily lead-gen run stopped itself early, to avoid burning "
        "scraping/OpenAI budget on a run whose output couldn't be trusted.\n\n"
        f"Reason: {message}\n\n"
        f"Log file: {_log_path}\n",
    )
    raise SystemExit(1)


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    global _crashed
    _crashed = True
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
    # Not on a crash: the crash alert already covers it, and mid-run failure
    # would leave the summary half-built anyway.
    if not _crashed:
        report = _build_summary_email()
        if report is not None:
            _send_alert(*report)
    if _log_handle is not None:
        try:
            _log_handle.flush()
            _log_handle.close()
        except OSError:
            pass
