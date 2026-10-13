import datetime
import fcntl
import logging
import logging.handlers
import os
import re
from typing import List, Optional

from .types import CheckerTaskMessage, EnoLogMessage

LOGGING_PREFIX = "##ENOLOGMESSAGE "

SNIP_SUFFIX = "... <SNIP>"
SLOT_FILE_SUFFIX = ".slots"

SIZE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?)I?B?\s*$", re.I)
SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(size: str) -> int:
    match = SIZE_PATTERN.match(size)
    if not match:
        raise ValueError(
            "invalid size {!r}, expected a byte count with an optional"
            " K, M, G or T suffix".format(size)
        )

    value, unit = match.groups()
    return int(float(value) * SIZE_UNITS[unit.upper()])


# Several handlers format the same record, so the interpolated message must stay
# a local: writing it back to record.msg makes the next handler re-apply the
# args to an already-formatted string.
def render_message(record: logging.LogRecord) -> str:
    if type(record.args) is tuple and len(record.args) > 0:
        try:
            return str(record.msg) % record.args
        except TypeError:
            pass
    return str(record.msg)


class DebugFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg: str = render_message(record)

        checker_task: Optional[CheckerTaskMessage] = getattr(
            record, "checker_task", None
        )

        timestamp: str = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%H:%M:%S.%f"
        )[:-3]
        method: str = getattr(checker_task, "method", None) or "<method>"
        levelname: str = getattr(record, "levelname", None) or "<level>"
        task_id: str = getattr(checker_task, "task_id", None) or "<taskid>"
        info_line: str = "{} {} {} {}".format(timestamp, levelname, method, task_id)

        log_lines: List[str] = [info_line]
        for i, line in enumerate(msg.strip().split("\n")):
            log_lines.append("    | " + line)

        return "\n".join(log_lines)


class JsonFormatter(logging.Formatter):
    prefix: str = ""
    max_message_size: Optional[int] = None
    max_record_size: Optional[int] = None

    def format(self, record: logging.LogRecord) -> str:
        msg: EnoLogMessage = self.create_message(record)

        if self.max_message_size is not None:
            self.truncate(msg, len(msg.message.encode()) - self.max_message_size)

        line: str = self.prefix + msg.model_dump_json(by_alias=True)
        if self.max_record_size is None:
            return line

        # The task context and the JSON escaping of the message both contribute
        # to the size of a record, so the only way to land below a byte limit is
        # to trim and re-render until the rendered line fits.
        for _ in range(8):
            excess: int = len(line.encode()) - self.max_record_size
            if excess <= 0 or not msg.message:
                break
            self.truncate(msg, excess)
            line = self.prefix + msg.model_dump_json(by_alias=True)

        return line

    def truncate(self, msg: EnoLogMessage, excess: int) -> None:
        if excess <= 0:
            return

        keep: int = len(msg.message.encode()) - excess - len(SNIP_SUFFIX)
        if keep <= 0:
            msg.message = ""
            return

        msg.message = (
            msg.message.encode()[:keep].decode("utf-8", errors="ignore") + SNIP_SUFFIX
        )

    def to_level(self, levelname: str) -> int:
        if levelname == "CRITICAL":
            return 4
        if levelname == "ERROR":
            return 3
        if levelname == "WARNING":
            return 2
        if levelname == "INFO":
            return 1
        if levelname == "DEBUG":
            return 0
        return 0

    def create_message(self, record: logging.LogRecord) -> EnoLogMessage:
        checker_task: Optional[CheckerTaskMessage] = getattr(
            record, "checker_task", None
        )
        service_name: Optional[str] = getattr(record, "service_name", None)
        checker_name: str = str(getattr(record, "checker_name", ""))
        return EnoLogMessage(
            tool=checker_name,
            type="infrastructure",
            severity=record.levelname,
            severity_level=self.to_level(record.levelname),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            message=render_message(record),
            module=record.module,
            function=record.funcName,
            service_name=service_name,
            task_id=getattr(checker_task, "task_id", None),
            method=getattr(checker_task, "method", None),
            team_id=getattr(checker_task, "team_id", None),
            team_name=getattr(checker_task, "team_name", None),
            current_round_id=getattr(checker_task, "current_round_id", None),
            related_round_id=getattr(checker_task, "related_round_id", None),
            flag=getattr(checker_task, "flag", None),
            variant_id=getattr(checker_task, "variant_id", None),
            task_chain_id=getattr(checker_task, "task_chain_id", None),
            flag_regex=getattr(checker_task, "flag_regex", None),
            flag_hash=getattr(checker_task, "flag_hash", None),
            attack_info=getattr(checker_task, "attack_info", None),
        )


class ELKFormatter(JsonFormatter):
    prefix = LOGGING_PREFIX
    # Elasticsearch rejects keyword values above 32766 bytes.
    max_message_size = 32766


# Marks the handlers this library installs, so that re-initialising a checker
# can discard its own handlers without touching the ones telemetry and the
# server attach to the same root logger.
class OwnedHandler:
    pass


class CheckerStreamHandler(OwnedHandler, logging.StreamHandler):  # type: ignore[type-arg]  # ty: ignore[missing-type-argument]
    pass


class BudgetFileHandler(OwnedHandler, logging.handlers.RotatingFileHandler):
    # The stock handler compares a record length in characters against a file
    # position in bytes, so multi-byte messages let a file grow past maxBytes.
    # A disk budget has to hold in bytes, hence the byte-accurate check.
    def shouldRollover(self, record: logging.LogRecord) -> int:
        # close() leaves the stream unset, which typeshed does not model.
        if self.stream is None:
            self.stream = self._open()

        if self.maxBytes <= 0:
            return 0

        pos: int = self.stream.tell()
        if not pos:
            return 0

        size: int = len(self.format(record).encode()) + len(self.terminator)
        return int(pos + size >= self.maxBytes)


def claim_log_slot(log_dir: str, name: str, slots: int) -> Optional[int]:
    # Byte range locks on one file hand every process writing to this directory
    # a distinct slot, which keeps the set of log files identical across
    # restarts instead of growing by a file set per process lifetime.
    fd: int = os.open(
        os.path.join(log_dir, name + SLOT_FILE_SUFFIX),
        os.O_CREAT | os.O_RDWR,
        0o644,
    )
    for slot in range(slots):
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, slot)
        except OSError:
            continue
        # The lock is held for the lifetime of the process, so the descriptor
        # is deliberately left open.
        return slot

    os.close(fd)
    return None
