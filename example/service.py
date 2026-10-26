"""
Toy vulnerable service checked by example.py's ExampleService checker.

Stores flags as notes keyed by an unguessable per-note token. Deliberately
vulnerable: fetching the reserved token "*" dumps every stored note, which is
what example.py's exploit_test relies on to recover a flag without ever
having been given its token.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI()

# In-memory mock db: token -> note content. Real persistence would need a
# database; a plain dict is enough for a service that only has to survive
# for the lifetime of one checker run.
notes: dict[str, str] = {}


class Note(BaseModel):
    token: str
    flag: str


@app.post("/note")
def store_note(note: Note) -> dict[str, str]:
    notes[note.token] = note.flag
    return {"status": "ok"}


@app.get("/note/{token}", response_class=PlainTextResponse)
def get_note(token: str) -> str:
    if token == "*":
        return "\n".join(notes.values())
    if token not in notes:
        raise HTTPException(status_code=404, detail="note not found")
    return notes[token]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=1337)
