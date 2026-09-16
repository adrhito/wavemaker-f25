"""The errors-only log exists so a fault can be found without reading a
whole day's INFO/SUCCESS lines. These tests prove the split actually holds:
WARNING-and-up (and only that) lands in logs/errors/, with a traceback when
there is one, and the existing logs/<date>.log handler keeps working exactly
as before.
"""

import logging

import pytest

from modules.logging import log_utils


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Point both handlers at tmp_path and reset the module's singletons.

    Without resetting `_file_handler` / `_error_handler`, a handler set up by
    an earlier test (or an earlier call in this same process) would just be
    returned as-is, still pointed at whatever path it was first created
    with -- these tests would silently pass by writing nowhere near tmp_path.
    """
    def _make_errors_dir():
        # Real ensure_directories() would create logs/errors/ before any
        # handler tries to open a file in it; mirror that here rather than
        # no-opping it away, or the lazily-opened FileHandler would fail with
        # "no such directory" the moment something actually logs an error.
        (tmp_path / "errors").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(log_utils, "_file_handler", None)
    monkeypatch.setattr(log_utils, "_error_handler", None)
    monkeypatch.setattr(log_utils.paths, "ensure_directories", _make_errors_dir)
    monkeypatch.setattr(log_utils.paths, "log_file", lambda: tmp_path / "day.log")
    monkeypatch.setattr(
        log_utils.paths, "error_log_file", lambda: tmp_path / "errors" / "day-errors.log"
    )

    logger = logging.getLogger(log_utils.LOGGER_NAME)
    yield tmp_path, logger

    # Handlers accumulate on the shared "logger" logger across tests unless
    # torn down; leaving one attached would leak into whichever test runs
    # next and double-log or double-count.
    for handler in list(logger.handlers):
        if handler in (log_utils._file_handler, log_utils._error_handler):
            logger.removeHandler(handler)
            handler.close()


def _errors_path(tmp_path):
    return tmp_path / "errors" / "day-errors.log"


class TestErrorLogSplit:
    def test_error_level_reaches_the_errors_file(self, isolated_logging):
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        logger.error("Piston 7 stuck: drive fault 0x40b7")
        log_utils._error_handler.flush()

        written = _errors_path(tmp_path).read_text(encoding="utf-8")
        assert "Piston 7 stuck: drive fault 0x40b7" in written

    def test_exception_includes_the_traceback(self, isolated_logging):
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        try:
            raise ValueError("bad home offset")
        except ValueError:
            logger.exception("Homing failed")
        log_utils._error_handler.flush()

        written = _errors_path(tmp_path).read_text(encoding="utf-8")
        assert "Homing failed" in written
        assert "Traceback (most recent call last):" in written
        assert "ValueError: bad home offset" in written

    def test_info_does_not_reach_the_errors_file(self, isolated_logging):
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        logger.info("Connected to PLC")
        logger.log(log_utils.SUCCESS, "Motors homed")
        log_utils._error_handler.flush()

        # An error-free run must leave no file at all -- that is the whole
        # point of a lazily-opened handler. If this ever exists, it should
        # be empty, not just missing the INFO/SUCCESS lines.
        errors_file = _errors_path(tmp_path)
        assert not errors_file.exists()

    def test_record_carries_enough_context_to_be_useful(self, isolated_logging):
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        logger.warning("Position 2 out of range")
        log_utils._error_handler.flush()

        written = _errors_path(tmp_path).read_text(encoding="utf-8")
        assert "WARNING" in written
        assert log_utils.LOGGER_NAME in written
        # module:lineno, e.g. "test_error_log:123" -- proves %(module)s and
        # %(lineno)d are both wired into ERROR_FORMAT, not just present in
        # the string.
        assert "test_error_log:" in written

    def test_main_log_is_unaffected_by_the_errors_handler(self, isolated_logging):
        """logs/<date>.log must keep receiving everything from SUCCESS up,
        unchanged by the new handler sitting alongside it."""
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        logger.log(log_utils.SUCCESS, "Ran a stroke.")
        logger.info("Connected to PLC")
        logger.error("Piston 7 stuck")
        log_utils._file_handler.flush()

        written = (tmp_path / "day.log").read_text(encoding="utf-8")
        assert "Ran a stroke." in written
        assert "Connected to PLC" in written
        assert "Piston 7 stuck" in written

    def test_errors_file_created_lazily_under_logs_errors(self, isolated_logging):
        """The errors/ folder exists (ensure_directories makes it up front,
        same as logs/ and analytics/), but the day's file inside it must not
        appear until something actually logs a WARNING or worse."""
        tmp_path, logger = isolated_logging
        log_utils.setup_file_logging()

        assert not _errors_path(tmp_path).exists()

        logger.error("first fault")
        log_utils._error_handler.flush()

        assert _errors_path(tmp_path).exists()

    def test_a_broken_errors_path_does_not_break_the_main_handler(
        self, isolated_logging, monkeypatch
    ):
        """If logs/errors/ can't be created or opened, the day's main log
        must still be set up -- one bad folder should not silence everything."""
        tmp_path, logger = isolated_logging

        def _boom():
            raise OSError("simulated: read-only filesystem")

        monkeypatch.setattr(log_utils.paths, "error_log_file", _boom)

        handler = log_utils.setup_file_logging()
        assert handler is not None
        assert log_utils._error_handler is None

        logger.info("still logging fine")
        handler.flush()
        assert "still logging fine" in (tmp_path / "day.log").read_text(encoding="utf-8")


def test_paths_wires_the_errors_directory():
    """app/paths.py must know about logs/errors/ so ensure_directories()
    creates it and error_log_file() names a file inside it."""
    from app import paths

    assert paths.ERROR_LOG_DIR == paths.LOG_DIR / "errors"
    assert paths.error_log_file().parent == paths.ERROR_LOG_DIR
    assert paths.error_log_file().name.endswith("-errors.log")
