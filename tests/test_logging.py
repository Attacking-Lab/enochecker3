import glob
import json
import logging
import logging.handlers
import os
from pathlib import Path

import pytest

from enochecker3 import Enochecker
from enochecker3.enochecker import resolve_worker_count
from enochecker3.logging import ELKFormatter, JsonFormatter, parse_size
from enochecker3.types import PutflagCheckerTaskMessage


def _task(**kwargs) -> PutflagCheckerTaskMessage:
    return PutflagCheckerTaskMessage(
        **{
            "task_id": 1,
            "method": "putflag",
            "address": "1.2.3.4",
            "team_id": 2,
            "team_name": "team",
            "current_round_id": 42,
            "related_round_id": 42,
            "flag": "ENOFLAG",
            "variant_id": 0,
            "timeout": 10000,
            "round_length": 60000,
            "task_chain_id": "chain",
            **kwargs,
        }
    )


def _checker(*args, **kwargs) -> Enochecker:
    """An Enochecker with its file logging set up, as the lifespan does it.

    The setup lives there rather than in the constructor so that a worker
    forked off a preloaded master gets a slot of its own.
    """
    checker = Enochecker(*args, **kwargs)
    checker._setup_file_logging()
    return checker


def _file_handler(checker: Enochecker) -> logging.handlers.RotatingFileHandler:
    return next(
        h
        for h in checker._logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )


def _record() -> logging.LogRecord:
    record = logging.LogRecord(
        "n", logging.INFO, "f.py", 1, "progress %s done", ("50%",), None
    )
    record.checker_name = "TestChecker"
    record.service_name = "Test"
    record.checker_task = _task()
    return record


def test_formatter_does_not_consume_record_args():
    record = _record()
    elk = ELKFormatter("%(message)s")
    plain = JsonFormatter("%(message)s")

    assert json.loads(elk.format(record).split(" ", 1)[1])["message"] == (
        "progress 50% done"
    )
    assert json.loads(plain.format(record))["message"] == "progress 50% done"


def test_file_handler_writes_queryable_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "64K")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "1")

    checker = _checker("Test", 1234)
    logger = checker._get_logger_adapter(_task())
    for i in range(200):
        logger.debug("filler %d %s", i, "x" * 100)

    files = glob.glob(os.path.join(str(tmp_path), "*.jsonl*"))
    assert len(files) == 4
    assert sum(os.path.getsize(f) for f in files) <= 64 * 1024

    entries = [
        json.loads(line)
        for f in files
        for line in Path(f).read_text().splitlines()
        if line
    ]
    assert entries
    for entry in entries:
        assert entry["currentRoundId"] == 42
        assert entry["taskChainId"] == "chain"
        assert entry["severity"] == "DEBUG"


def test_parse_size():
    assert parse_size("1024") == 1024
    assert parse_size("8K") == 8 * 1024
    assert parse_size("1g") == 1024**3
    assert parse_size(" 1.5 GiB ") == int(1.5 * 1024**3)
    assert parse_size("2TB") == 2 * 1024**4

    for invalid in ["", "abc", "1P", "-1M", "1 M B"]:
        with pytest.raises(ValueError):
            parse_size(invalid)


def test_worker_count_sources(monkeypatch):
    monkeypatch.delenv("ENOCHECKER_LOG_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr("sys.argv", ["gunicorn", "example:checker.app"])
    assert resolve_worker_count() == 1

    for args in (["-w", "3"], ["-w3"], ["--workers", "3"], ["--workers=3"]):
        monkeypatch.setattr("sys.argv", ["gunicorn"] + args + ["example:app"])
        assert resolve_worker_count() == 3

    monkeypatch.setattr("sys.argv", ["gunicorn", "--workers"])
    assert resolve_worker_count() == 1

    monkeypatch.setenv("WEB_CONCURRENCY", "5")
    assert resolve_worker_count() == 5

    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "7")
    assert resolve_worker_count() == 7

    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "0")
    with pytest.raises(ValueError):
        resolve_worker_count()

    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "many")
    with pytest.raises(ValueError):
        resolve_worker_count()


def test_budget_is_split_between_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "1M")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "2")

    checker = _checker("Test", 1234)
    handler = _file_handler(checker)
    assert handler.maxBytes == 1024 * 1024 // (2 * 4)
    assert handler.backupCount == 3


def test_budget_holds_for_oversized_and_multibyte_records(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "64K")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "1")

    checker = _checker("Test", 1234)
    logger = checker._get_logger_adapter(_task())

    # a single record far above the per file size, and one whose characters are
    # four bytes each, both of which the stock handler would write unbounded
    logger.debug("huge %s", "x" * 1024 * 1024)
    for _ in range(20):
        logger.debug("wide %s", "\U0001f600" * 4000)

    files = glob.glob(os.path.join(str(tmp_path), "*.jsonl*"))
    assert sum(os.path.getsize(f) for f in files) <= 64 * 1024
    assert all(os.path.getsize(f) <= 64 * 1024 // 4 for f in files)

    entries = [
        json.loads(line)
        for f in files
        for line in Path(f).read_text().splitlines()
        if line
    ]
    assert entries
    assert any(e["message"].endswith("... <SNIP>") for e in entries)


def test_slots_are_reused_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "1M")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "1")

    paths = set()
    for _ in range(3):
        checker = _checker("Test", 1234)
        paths.add(_file_handler(checker).baseFilename)
        _file_handler(checker).close()

    assert len(paths) == 1
    assert glob.glob(os.path.join(str(tmp_path), "*.jsonl")) == list(paths)


def test_budget_below_the_minimum_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "16K")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "1")

    with pytest.raises(ValueError, match="too small"):
        _checker("Test", 1234)


def test_unusable_log_dir_is_fatal(tmp_path, monkeypatch):
    # A path whose parent is a file rather than an unreadable directory: root
    # bypasses directory permissions, and the tests run as root in CI.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(blocked / "logs"))
    monkeypatch.delenv("ENOCHECKER_DROP_LOG_OVERRUN", raising=False)

    with pytest.raises(OSError):
        _checker("Test", 1234)

    monkeypatch.setenv("ENOCHECKER_DROP_LOG_OVERRUN", "1")
    checker = _checker("Test", 1234)
    with pytest.raises(StopIteration):
        _file_handler(checker)


def test_malformed_budget_stays_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_DROP_LOG_OVERRUN", "1")
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "plenty")

    with pytest.raises(ValueError):
        _checker("Test", 1234)


def test_preloaded_workers_each_get_their_own_slot(tmp_path, monkeypatch):
    # The shape gunicorn preload_app produces: one checker built in the
    # master, the workers forked off it. Each has to claim a slot of its own,
    # or they rotate each other's files.
    monkeypatch.setenv("ENOCHECKER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ENOCHECKER_LOG_BUDGET", "1M")
    monkeypatch.setenv("ENOCHECKER_LOG_WORKERS", "3")

    checker = Enochecker("Test", 1234)
    with pytest.raises(StopIteration):
        _file_handler(checker)

    claimed_read, claimed_write = os.pipe()
    # A slot is a lock held for the lifetime of the process, so a worker that
    # exited would hand its own slot to the next one and hide a collision.
    # None of them leaves before every one of them has claimed.
    gate_read, gate_write = os.pipe()
    children = []
    for worker in range(3):
        pid = os.fork()
        if pid == 0:
            os.close(claimed_read)
            os.close(gate_write)
            code = 0
            try:
                checker._setup_file_logging()
                path = _file_handler(checker).baseFilename
                os.write(claimed_write, (path + "\n").encode())
                checker._get_logger_adapter(_task()).debug("worker %d", worker)
                os.read(gate_read, 1)
            except BaseException:
                code = 1
            finally:
                os._exit(code)
        children.append(pid)
    os.close(claimed_write)
    os.close(gate_read)

    paths = set()
    with os.fdopen(claimed_read) as pipe:
        for _ in children:
            paths.add(pipe.readline().strip())
        os.close(gate_write)
        for pid in children:
            assert os.waitpid(pid, 0)[1] == 0

    assert len(paths) == 3
    messages = [
        json.loads(line)["message"]
        for path in sorted(paths)
        for line in Path(path).read_text().splitlines()
        if line
    ]
    assert sorted(messages) == ["worker 0", "worker 1", "worker 2"]
