"""
Demo webhook service (PRD §91).

Lets EventMesh be demonstrated end-to-end with no external dependency.
The receiver's behavior is controlled at runtime via POST /_control so
demo scripts (and failure tests) can flip it between "always 200",
"always 500", "always 429", "delay N seconds", or "disconnect" without
restarting the process.
"""
import asyncio
import hashlib
import hmac
import time

from fastapi import FastAPI, Header, Request, Response

app = FastAPI(title="EventMesh Demo Webhook")

_STATE = {"mode": "success", "delay_seconds": 0.0, "received": []}


@app.post("/_control")
async def set_mode(request: Request):
    body = await request.json()
    _STATE["mode"] = body.get("mode", "success")
    _STATE["delay_seconds"] = float(body.get("delay_seconds", 0.0))
    return {"mode": _STATE["mode"], "delay_seconds": _STATE["delay_seconds"]}


@app.get("/_control")
async def get_mode():
    return {"mode": _STATE["mode"], "delay_seconds": _STATE["delay_seconds"], "received_count": len(_STATE["received"])}


@app.get("/_received")
async def received():
    return _STATE["received"][-50:]


@app.post("/events")
async def receive_event(request: Request, x_eventmesh_signature: str | None = Header(default=None)):
    if _STATE["delay_seconds"] > 0:
        await asyncio.sleep(_STATE["delay_seconds"])

    mode = _STATE["mode"]
    if mode == "disconnect":
        # Simulate a connection drop by returning nothing meaningful;
        # httpx will typically surface this as a transport error on
        # very short-lived servers. For local demo purposes a 499-style
        # response is used as the closest FastAPI-expressible analog.
        return Response(status_code=499)
    if mode == "timeout":
        await asyncio.sleep(3600)  # will trigger the client's read timeout
    if mode == "500":
        return Response(status_code=500, content="simulated server error")
    if mode == "429":
        return Response(status_code=429, headers={"Retry-After": "5"}, content="simulated rate limit")
    if mode == "400":
        return Response(status_code=400, content="simulated bad request")

    body = await request.body()
    _STATE["received"].append({
        "signature_header": x_eventmesh_signature,
        "body": body.decode(errors="replace"),
        "received_at": time.time(),
    })
    return {"status": "received"}
