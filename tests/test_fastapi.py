from typing import Union
from unittest.mock import patch

import mongomock
import pymongo
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from enochecker3 import (
    Enochecker,
    EnocheckerException,
    ExploitCheckerTaskMessage,
    GetflagCheckerTaskMessage,
    GetnoiseCheckerTaskMessage,
    PutflagCheckerTaskMessage,
    PutnoiseCheckerTaskMessage,
    TaskMetadata,
    TestCheckerTaskMessage,
)
from enochecker3.enochecker import MONGO_HEALTH_PROBE_TIMEOUT_S
from enochecker3.types import (
    CheckerHealthMessage,
    CheckerHealthStatus,
    CheckerResultMessage,
    CheckerTaskResult,
)


@pytest.mark.asyncio
async def test_get_service_info():
    """Test that get_service_info returns correct checker information."""
    checker = Enochecker("test_service", 8080)

    @checker.putflag(0)
    @checker.putflag(1)
    async def putflag_test() -> str:
        return "attack_info"

    @checker.getflag(0)
    @checker.getflag(1)
    async def getflag_test() -> None:
        pass

    @checker.exploit(0, 0)
    async def exploit_test() -> str:
        return "ENO{flag}"

    info = checker.get_service_info()

    assert info.service_name == "test_service"
    assert info.flag_variants == 2
    assert info.noise_variants == 0
    assert info.exploit_variants == [0]
    assert info.havoc_variants == 0


@pytest.mark.parametrize(
    "task",
    [
        PutflagCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
            flag="test",
        ),
        GetflagCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
            flag="test",
        ),
        PutnoiseCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
        ),
        GetnoiseCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
        ),
        ExploitCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
            flag_regex="test",
            flag_hash="test",
            attack_info="attack_info",
        ),
        TestCheckerTaskMessage(
            task_id=0,
            address="127.0.0.1",
            team_id=0,
            team_name="team_name",
            current_round_id=0,
            related_round_id=0,
            variant_id=0,
            timeout=15000,
            round_length=60000,
            task_chain_id="task_chain_id",
        ),
    ],
)
def test_fastapi_app_property(
    checker: Enochecker,
    task: Union[
        PutflagCheckerTaskMessage,
        GetflagCheckerTaskMessage,
        PutnoiseCheckerTaskMessage,
        GetnoiseCheckerTaskMessage,
        ExploitCheckerTaskMessage,
        TestCheckerTaskMessage,
    ],
):
    @checker.putflag(0)
    async def putflag_test() -> str:
        return "attack_info"

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    @checker.putnoise(0)
    async def putnoise_test() -> None:
        pass

    @checker.getnoise(0)
    async def getnoise_test() -> None:
        pass

    @checker.exploit(0, 0)
    async def exploit_test() -> None:
        pass

    app = checker.app
    client = TestClient(app)

    assert isinstance(app, FastAPI)

    # Check that routes are registered
    routes = [route.path for route in app.routes]
    assert "/service" in routes
    assert "/health" in routes
    assert "/" in routes

    result = client.post("/", json=task.model_dump())
    result.raise_for_status()
    result_model = CheckerResultMessage(**result.json())
    if isinstance(task, PutflagCheckerTaskMessage):
        assert result_model.attack_info == "attack_info"


def test_fastapi_app_returns_internal_error_for_method_specific_validation_failure(
    checker: Enochecker,
):
    """A task whose declared method requires a field the request omits must
    surface as an INTERNAL_ERROR result, not an unhandled 500."""

    @checker.putflag(0)
    async def putflag_test() -> str:
        return "attack_info"

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    app = checker.app
    client = TestClient(app)

    task = PutflagCheckerTaskMessage(
        task_id=0,
        address="127.0.0.1",
        team_id=0,
        team_name="team_name",
        current_round_id=0,
        related_round_id=0,
        variant_id=0,
        timeout=15000,
        round_length=60000,
        task_chain_id="task_chain_id",
        flag="test",
    )
    payload = task.model_dump()
    del payload["flag"]

    result = client.post("/", json=payload)

    assert result.status_code == 200
    result_model = CheckerResultMessage(**result.json())
    assert result_model.result == CheckerTaskResult.INTERNAL_ERROR


def test_fastapi_app_returns_metadata_under_camel_key(checker: Enochecker):
    """Metadata a handler writes is serialized back under the `metadata` key."""

    @checker.putflag(0)
    async def putflag_test(metadata: TaskMetadata) -> str:
        metadata["attempts"] = 3
        metadata["nested"] = {"k": [1, 2]}
        return "attack_info"

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    client = TestClient(checker.app)

    task = PutflagCheckerTaskMessage(
        task_id=0,
        address="127.0.0.1",
        team_id=0,
        team_name="team_name",
        current_round_id=0,
        related_round_id=0,
        variant_id=0,
        timeout=15000,
        round_length=60000,
        task_chain_id="task_chain_id",
        flag="test",
    )

    result = client.post("/", json=task.model_dump())
    result.raise_for_status()
    body = result.json()

    assert body["metadata"] == {"attempts": 3, "nested": {"k": [1, 2]}}
    assert CheckerResultMessage(**body).metadata == {
        "attempts": 3,
        "nested": {"k": [1, 2]},
    }


def test_health_defaults_to_a_mongo_ping(checker: Enochecker):
    """With no handler of its own, the checker answers for the dependency it
    always has - so the client has to be up, which means going through the
    lifespan rather than around it."""

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    with TestClient(checker.app) as client:
        with patch.object(
            mongomock.database.Database, "command", return_value={"ok": 1.0}
        ) as ping:
            result = client.get("/health")

    assert result.status_code == 200
    assert result.json() == {"status": "ok"}
    ping.assert_called_once_with("ping")


def test_health_reports_an_unreachable_mongo(checker: Enochecker):
    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    with TestClient(checker.app, raise_server_exceptions=False) as client:
        with patch.object(
            mongomock.database.Database,
            "command",
            side_effect=ServerSelectionTimeoutError("no server available"),
        ):
            result = client.get("/health")

    assert result.status_code == 503
    parsed = CheckerHealthMessage.model_validate(result.json())
    assert parsed.status == CheckerHealthStatus.ERROR
    assert parsed.detail is not None
    assert "MongoDB unreachable" in parsed.detail
    assert "no server available" in parsed.detail


def test_health_reports_a_checker_that_has_not_finished_starting(
    checker: Enochecker,
):
    """Uvicorn does not serve before the lifespan completes, so this is only
    reachable by skipping it - but reporting ok without a client to vouch for
    would be a lie wherever it happens."""

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    result = TestClient(checker.app, raise_server_exceptions=False).get("/health")

    assert result.status_code == 503
    assert "starting up" in result.json()["detail"]


def test_health_ping_is_bounded(checker: Enochecker):
    """An unbounded ping would sit on pymongo's 30s server selection default,
    which outlasts any prober that would be asking."""

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    with TestClient(checker.app) as client:
        with patch(
            "enochecker3.enochecker.pymongo.timeout", wraps=pymongo.timeout
        ) as bound:
            client.get("/health")

    bound.assert_called_once_with(MONGO_HEALTH_PROBE_TIMEOUT_S)


def test_health_override_returning_none_keeps_default_body(checker: Enochecker):
    calls = []

    @checker.health
    async def health() -> None:
        calls.append(True)

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    result = TestClient(checker.app).get("/health")

    assert result.status_code == 200
    assert result.json() == {"status": "ok"}
    assert calls == [True]


def test_health_override_body_is_used_verbatim(checker: Enochecker):
    """A handler may report more than the bare status, so its body is not
    narrowed to a fixed schema."""

    @checker.health
    def health() -> dict:
        return {"status": "ok", "mongo": "up"}

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    result = TestClient(checker.app).get("/health")

    assert result.status_code == 200
    assert result.json() == {"status": "ok", "mongo": "up"}


def test_health_override_raising_reports_unavailable(checker: Enochecker):
    @checker.health
    async def health() -> None:
        raise RuntimeError("mongo unreachable")

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    result = TestClient(checker.app, raise_server_exceptions=False).get("/health")

    assert result.status_code == 503
    assert result.json() == {"status": "error", "detail": "mongo unreachable"}


def test_health_override_http_exception_picks_status_code(checker: Enochecker):
    @checker.health
    async def health() -> None:
        raise HTTPException(status_code=429, detail="too many probes")

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    result = TestClient(checker.app, raise_server_exceptions=False).get("/health")

    assert result.status_code == 429
    assert result.json() == {"detail": "too many probes"}


def test_health_rejects_second_definition(checker: Enochecker):
    @checker.health
    async def health() -> None:
        pass

    with pytest.raises(EnocheckerException):

        @checker.health
        async def health_again() -> None:
            pass


def test_health_bodies_conform_to_the_core_model(checker: Enochecker):
    """Both bodies the library emits itself are `CheckerHealthMessage`, so a
    consumer can validate them instead of matching keys by hand."""

    @checker.putflag(0)
    async def putflag_test() -> None:
        pass

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    with TestClient(checker.app) as client:
        ok = client.get("/health")
    assert (
        CheckerHealthMessage.model_validate(ok.json()).status == CheckerHealthStatus.OK
    )

    @checker.health
    async def health() -> None:
        raise RuntimeError("mongo unreachable")

    error = TestClient(checker.app, raise_server_exceptions=False).get("/health")
    parsed = CheckerHealthMessage.model_validate(error.json())
    assert parsed.status == CheckerHealthStatus.ERROR
    assert parsed.detail == "mongo unreachable"
