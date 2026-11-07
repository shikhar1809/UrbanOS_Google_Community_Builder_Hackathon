"""The bridge from Firebase's request model into the ASGI app.

Found in production: every streamed response hung until the 300s timeout at full
CPU, which starved the instance and took the dashboards down with it.
"""

import io
import threading

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import StreamingResponse

import main

probe = FastAPI()
ran_after_response = []


@probe.get("/stream")
async def stream():
    async def chunks():
        yield b"hello "
        yield b"world"
    return StreamingResponse(chunks(), media_type="text/plain")


@probe.get("/stream-error")
async def stream_error():
    async def chunks():
        raise RuntimeError("upstream refused")
        yield b""
    return StreamingResponse(chunks())


@probe.post("/echo")
async def echo(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(ran_after_response.append, True)
    return {"body": (await request.body()).decode()}


def call(method, path, body=b"", deadline=10):
    """Run on a daemon thread, so a regression fails here instead of hanging the suite."""
    class Req:
        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "SERVER_NAME": "x",
            "SERVER_PORT": "80", "REMOTE_ADDR": "127.0.0.1", "REMOTE_PORT": "1",
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
        }

    out = {}

    def run():
        try:
            out["response"] = main._run_asgi(probe, Req)
        except Exception as e:
            out["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(deadline)
    assert not worker.is_alive(), f"{method} {path} hung in the bridge"
    return out


def test_a_streamed_response_finishes():
    response = call("GET", "/stream")["response"]

    assert response.status_code == 200
    assert response.get_data() == b"hello world"


def test_a_failing_stream_ends_instead_of_hanging():
    """A photo Twilio refuses must fail fast, not hold a thread."""
    assert "error" in call("GET", "/stream-error")


def test_a_normal_request_still_reads_its_body_and_runs_background_tasks():
    ran_after_response.clear()

    response = call("POST", "/echo", b"citizen report")["response"]

    assert response.get_json() == {"body": "citizen report"}
    assert ran_after_response == [True]
