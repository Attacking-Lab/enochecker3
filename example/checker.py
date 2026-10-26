import secrets
from typing import Optional

from httpx import AsyncClient
from pymongo import AsyncMongoClient

from enochecker3 import (
    ChainDB,
    Enochecker,
    FlagSearcher,
    GetflagCheckerTaskMessage,
    PutflagCheckerTaskMessage,
)
from enochecker3.utils import assert_equals, assert_in

DEFAULT_SERVICE_PORT = 1337


def build_checker(
    *,
    mongo_client: Optional[AsyncMongoClient] = None,
    service_port: int = DEFAULT_SERVICE_PORT,
) -> Enochecker:
    checker = Enochecker("ExampleService", service_port, mongo_client=mongo_client)

    @checker.putflag(0)
    async def putflag_test(
        task: PutflagCheckerTaskMessage,
        client: AsyncClient,
        db: ChainDB,
    ) -> None:
        token = secrets.token_hex(32)
        r = await client.post("/note", json={"token": token, "flag": task.flag})
        assert_equals(r.status_code, 200, "storing note with flag failed")

        await db.set("token", token)

    @checker.getflag(0)
    async def getflag_test(
        task: GetflagCheckerTaskMessage, client: AsyncClient, db: ChainDB
    ) -> None:
        token = await db.get("token")

        r = await client.get(f"/note/{token}")
        assert_equals(r.status_code, 200, "getting note with flag failed")
        assert_in(task.flag, r.text, "flag missing from note")

    @checker.exploit(0, 0)
    async def exploit_test(
        searcher: FlagSearcher, client: AsyncClient
    ) -> Optional[str]:
        r = await client.get(
            "/note/*",
        )
        assert not r.is_error

        if flag := searcher.search_flag(r.text):
            return flag
        return None

    return checker


checker = build_checker()
app = lambda: checker.app


if __name__ == "__main__":
    checker.run()
