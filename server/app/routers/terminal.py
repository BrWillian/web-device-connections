import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import origin_allowed, redeem_grant

from ..state import connected_clients, device_queues

logger = logging.getLogger(__name__)
router = APIRouter()

WS_UNAUTHORIZED = 4401


@router.websocket("/terminal/{device_id}")
async def terminal_to_device(ws: WebSocket, device_id: str):
    await ws.accept()

    if not origin_allowed(ws.headers.get("origin")):
        await ws.send_text(json.dumps({"error": "origem não permitida"}))
        await ws.close(code=WS_UNAUTHORIZED)
        return

    # A browser cannot set an Authorization header on a WebSocket handshake, so it
    # presents a single-use grant that the frontend signed after logging the user in.
    if await redeem_grant(ws.query_params.get("grant"), device_id, "terminal") is None:
        await ws.send_text(json.dumps({"error": "autorização inválida ou expirada"}))
        await ws.close(code=WS_UNAUTHORIZED)
        return

    device_ws = connected_clients.get(device_id)
    if device_ws is None:
        await ws.send_text(json.dumps({"error": "Dispositivo não conectado"}))
        await ws.close()
        return

    session_id = None
    output_task = None

    try:
        while True:
            try:
                data = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                logger.debug("[terminal/%s] non-JSON frame ignored", device_id)
                continue

            msg_type = data.get("type")

            if msg_type == "start_session":
                session_id = data.get("session_id")
                if not session_id:
                    continue
                queue: asyncio.Queue = asyncio.Queue()
                device_queues.setdefault(device_id, {})[session_id] = queue

                await device_ws.send_text(
                    json.dumps({"type": "start_session", "session_id": session_id})
                )

                async def send_output(q: asyncio.Queue = queue) -> None:
                    try:
                        while True:
                            await ws.send_text(await q.get())
                    except (WebSocketDisconnect, asyncio.CancelledError):
                        pass
                    except Exception as exc:
                        logger.debug("[terminal/%s] output pump stopped: %s", device_id, exc)

                output_task = asyncio.create_task(send_output())

            elif msg_type in ("input", "resize") and session_id:
                await device_ws.send_text(json.dumps(data))

    except WebSocketDisconnect:
        logger.info("terminal session closed for %s (session %s)", device_id, session_id)
    finally:
        if output_task is not None:
            output_task.cancel()
        sessions = device_queues.get(device_id)
        if sessions and session_id in sessions:
            sessions.pop(session_id, None)
