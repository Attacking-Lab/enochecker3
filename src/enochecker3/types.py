__all__ = [
    "CheckerMethod",
    "CheckerTaskMessage",
    "CheckerHealthMessage",
    "CheckerHealthStatus",
    "CheckerInfoMessage",
    "CheckerResultMessage",
    "CheckerTaskResult",
    "EnoLogMessage",
]

from typing import Any, Dict, Literal, Optional

from enochecker_core import (
    CheckerHealthMessage,
    CheckerHealthStatus,
    CheckerInfoMessage,
    CheckerMethod,
    CheckerResultMessage,
    CheckerTaskMessage,
    CheckerTaskResult,
    EnoLogMessage,
)


class TaskMetadata(Dict[str, Any]):
    """Mutable per-task metadata bag.

    Declare a `metadata: TaskMetadata` parameter on any checker method and
    populate it during the run; whatever it contains is returned to the engine
    under the result message's ``metadata`` key (dropped from the wire when
    left empty). Values must be JSON-serializable.
    """


class PutflagCheckerTaskMessage(CheckerTaskMessage):
    flag: str
    method: Literal[CheckerMethod.PUTFLAG] = CheckerMethod.PUTFLAG


class GetflagCheckerTaskMessage(CheckerTaskMessage):
    flag: str
    method: Literal[CheckerMethod.GETFLAG] = CheckerMethod.GETFLAG


class PutnoiseCheckerTaskMessage(CheckerTaskMessage):
    method: Literal[CheckerMethod.PUTNOISE] = CheckerMethod.PUTNOISE


class GetnoiseCheckerTaskMessage(CheckerTaskMessage):
    method: Literal[CheckerMethod.GETNOISE] = CheckerMethod.GETNOISE


class HavocCheckerTaskMessage(CheckerTaskMessage):
    method: Literal[CheckerMethod.HAVOC] = CheckerMethod.HAVOC


class ExploitCheckerTaskMessage(CheckerTaskMessage):
    flag_regex: str
    flag_hash: str
    attack_info: Optional[str]
    method: Literal[CheckerMethod.EXPLOIT] = CheckerMethod.EXPLOIT


class TestCheckerTaskMessage(CheckerTaskMessage):
    __test__ = False  # Tell pytest not to collect this as a test class
    method: Literal[CheckerMethod.TEST] = CheckerMethod.TEST


class BaseException(Exception):
    def __init__(
        self, message: Optional[str], log_message: Optional[str] = None
    ) -> None:
        self.message: Optional[str] = message
        self.log_message: Optional[str] = log_message
        # Filled in by Enochecker._call_method with whatever metadata (e.g.
        # recorded flows) had already accumulated before this exception cut
        # the checker method short, so it still reaches the result message.
        self.metadata: Optional[TaskMetadata] = None


class MumbleException(BaseException):
    pass


class OfflineException(BaseException):
    pass


class InternalErrorException(BaseException):
    def __init__(
        self,
        message: Optional[str],
        log_message: Optional[str] = None,
        inner: Optional[Exception] = None,
    ) -> None:
        super().__init__(message, log_message)
        self.inner: Optional[Exception] = inner
