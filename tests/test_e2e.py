"""
End-to-end test: the actual example checker (example.py) talking over real
HTTP to the actual example service (example_service.py), backed by
MockAsyncMongoClient instead of a real MongoDB. Runs a full
putflag -> getflag -> exploit round trip, unlike the other tests which drive
individual dependencies/handlers in isolation.
"""

import hashlib
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi.testclient import TestClient

from enochecker3 import (
    ExploitCheckerTaskMessage,
    GetflagCheckerTaskMessage,
    PutflagCheckerTaskMessage,
)
from enochecker3.mock import MockAsyncMongoClient
from enochecker_core import CheckerTaskResult

from example import build_checker
from example_service import app as note_service_app
from example_service import notes as note_service_notes

FLAG = "ENOabcdefgh="
FLAG_REGEX = r"ENO.{8}="
FLAG_HASH = hashlib.sha256(FLAG.encode()).hexdigest()

TASK_DEFAULTS = dict(
    task_id=1,
    address="127.0.0.1",
    team_id=1,
    team_name="test-team",
    current_round_id=1,
    related_round_id=1,
    variant_id=0,
    timeout=30000,
    round_length=60000,
    task_chain_id="task-chain-1",
)


@contextmanager
def running_note_service(port: int) -> Iterator[None]:
    config = uvicorn.Config(
        note_service_app, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("example_service did not start in time")
            time.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def note_service_port() -> Iterator[int]:
    note_service_notes.clear()
    port = 18337
    with running_note_service(port):
        yield port
    note_service_notes.clear()


def test_example_checker_against_example_service_e2e(note_service_port: int):
    checker = build_checker(
        mongo_client=MockAsyncMongoClient(), service_port=note_service_port
    )

    with TestClient(checker.app) as client:
        putflag_task = PutflagCheckerTaskMessage(**TASK_DEFAULTS, flag=FLAG)
        putflag_response = client.post("/", json=putflag_task.model_dump())
        putflag_response.raise_for_status()
        assert putflag_response.json()["result"] == CheckerTaskResult.OK.value

        # The flag must actually have made it into the service's own store.
        assert FLAG in note_service_notes.values()

        getflag_task = GetflagCheckerTaskMessage(**TASK_DEFAULTS, flag=FLAG)
        getflag_response = client.post("/", json=getflag_task.model_dump())
        getflag_response.raise_for_status()
        assert getflag_response.json()["result"] == CheckerTaskResult.OK.value

        exploit_task = ExploitCheckerTaskMessage(
            **TASK_DEFAULTS,
            flag_regex=FLAG_REGEX,
            flag_hash=FLAG_HASH,
            attack_info=None,
        )
        exploit_response = client.post("/", json=exploit_task.model_dump())
        exploit_response.raise_for_status()
        exploit_result = exploit_response.json()
        assert exploit_result["result"] == CheckerTaskResult.OK.value
        assert exploit_result["flag"] == FLAG


def test_example_checker_getflag_fails_without_prior_putflag_e2e(
    note_service_port: int,
):
    checker = build_checker(
        mongo_client=MockAsyncMongoClient(), service_port=note_service_port
    )

    with TestClient(checker.app) as client:
        getflag_task = GetflagCheckerTaskMessage(**TASK_DEFAULTS, flag=FLAG)
        getflag_response = client.post("/", json=getflag_task.model_dump())
        getflag_response.raise_for_status()
        assert getflag_response.json()["result"] != CheckerTaskResult.OK.value
