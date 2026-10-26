# Testing

## Running the checker locally

**Swagger UI** (interactive):
```bash
uvicorn example.checker:checker.app --reload
# Browse to http://localhost:8000/docs
```

**curl**:
```bash
curl -X POST http://localhost:8000/ -H "Content-Type: application/json" -d '{
  "method": "putflag", "address": "localhost", "teamId": 1, "teamName": "Test",
  "currentRoundId": 1, "relatedRoundId": 1, "flag": "ENO{test}", "variantId": 0,
  "timeout": 30000, "roundLength": 60000, "taskChainId": "test", "taskId": 1
}'
```

**Python API**:
```python
import asyncio
from enochecker3 import PutflagCheckerTaskMessage
from enochecker_core import CheckerMethod

async def test():
    await checker._init()
    task = PutflagCheckerTaskMessage(
        task_id=1, method=CheckerMethod.PUTFLAG, address="localhost",
        team_id=1, team_name="Test", current_round_id=1, related_round_id=1,
        flag="ENO{test}", variant_id=0, timeout=30000,
        round_length=60000, task_chain_id="test"
    )
    result = await checker._call_putflag(task)
    print(result)

asyncio.run(test())
```

## MongoDB

ChainDB and the other MongoDB-backed dependencies (`AsyncCollection`, `AsyncDatabase`) need *some* MongoDB, but for tests you don't need a real server or Docker. Pass an in-memory `MockAsyncMongoClient` instead (requires the `testing` extra):

```
pip install enochecker3[testing]
```

```python
from enochecker3 import Enochecker
from enochecker3.mock import MockAsyncMongoClient

checker = Enochecker("ExampleService", 1337, mongo_client=MockAsyncMongoClient())

async def test():
    await checker._init()
    result = await checker._call_putflag(task)
    ...
```

State lives only in the Python process, so each `MockAsyncMongoClient()` starts empty and nothing needs cleaning up between test runs.

To run against a real server instead:
```bash
docker run -d -p 27017:27017 mongo:latest
```

Or point at an existing one with environment variables:
```bash
export MONGO_HOST=mongodb.example.com
export MONGO_PORT=27017
export MONGO_USER=checker
export MONGO_PASSWORD=secret
```
