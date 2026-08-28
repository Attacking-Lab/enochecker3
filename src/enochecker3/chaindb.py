from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.trace import get_current_span
from pymongo.asynchronous.collection import AsyncCollection

from .types import MumbleException


class ChainDB:
    def __init__(
        self, collection: AsyncCollection[Dict[str, Any]], task_chain_id: str
    ) -> None:
        self.collection: AsyncCollection[Dict[str, Any]] = collection
        self.task_chain_id: str = task_chain_id

    async def get(self, key: str) -> Any:
        with (
            trace.get_tracer_provider()
            .get_tracer(__name__)
            .start_as_current_span(f"ChainDB.get({key})")
        ):
            val: Optional[Any] = await self.collection.find_one(
                {
                    "task_chain_id": self.task_chain_id,
                    "key": key,
                }
            )
            if val is None:
                raise MumbleException(
                    f"Key {key} not found in ChainDB",
                    log_message=f"ChainDB.get({key}) found no entry for "
                    f"task_chain_id {self.task_chain_id}",
                )
            get_current_span().set_attribute(f"chaindb.value.{key}", val["value"])
            return val["value"]

    async def set(self, key: str, val: Any) -> None:
        with (
            trace.get_tracer_provider()
            .get_tracer(__name__)
            .start_as_current_span(f"ChainDB.set({key})")
        ):
            await self.collection.replace_one(
                {
                    "task_chain_id": self.task_chain_id,
                    "key": key,
                },
                {
                    "task_chain_id": self.task_chain_id,
                    "key": key,
                    "value": val,
                },
                upsert=True,
            )
            get_current_span().set_attribute(f"chaindb.value.{key}", val)
