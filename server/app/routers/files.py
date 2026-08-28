import hashlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import origin_allowed, redeem_grant

from ..state import active_downloads, active_uploads, connected_clients

logger = logging.getLogger(__name__)
router = APIRouter()

WS_UNAUTHORIZED = 4401


async def _authorize(ws: WebSocket, device_id: str, scope: str) -> WebSocket | None:
    """Check the origin and grant, then resolve the device socket.

    Returns None after closing the socket if anything does not check out.
    """
    if not origin_allowed(ws.headers.get("origin")):
        await ws.send_text(json.dumps({"error": "origem não permitida"}))
        await ws.close(code=WS_UNAUTHORIZED)
        return None

    if await redeem_grant(ws.query_params.get("grant"), device_id, scope) is None:
        await ws.send_text(json.dumps({"error": "autorização inválida ou expirada"}))
        await ws.close(code=WS_UNAUTHORIZED)
        return None

    device_ws = connected_clients.get(device_id)
    if device_ws is None:
        await ws.send_text(json.dumps({"error": "Dispositivo não conectado"}))
        await ws.close()
        return None
    return device_ws


@router.websocket("/file/{device_id}")
async def file_upload(ws: WebSocket, device_id: str):
    await ws.accept()

    device_ws = await _authorize(ws, device_id, "upload")
    if device_ws is None:
        return

    active_uploads[device_id] = ws

    try:
        try:
            meta = json.loads(await ws.receive_text())
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"error": "Metadados inválidos"}))
            await ws.close()
            return

        filename = meta.get("filename")
        if not filename:
            await ws.send_text(json.dumps({"error": "'filename' é obrigatório"}))
            await ws.close()
            return

        await device_ws.send_text(
            json.dumps(
                {
                    "type": "file_begin",
                    "filename": filename,
                    "size": meta.get("size"),
                    "target_path": meta.get("target_path"),
                }
            )
        )

        hasher = hashlib.sha256()
        canceled = False

        while True:
            packet = await ws.receive()
            if packet.get("type") == "websocket.disconnect":
                break

            chunk = packet.get("bytes")
            if chunk is not None:
                hasher.update(chunk)
                await device_ws.send_bytes(chunk)
                continue

            text = packet.get("text")
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "file_complete":
                break
            if msg.get("type") == "file_cancel":
                canceled = True
                try:
                    await device_ws.send_text(
                        json.dumps({"type": "file_cancel", "filename": filename})
                    )
                except Exception:
                    pass
                break

        if not canceled:
            try:
                await device_ws.send_text(
                    json.dumps({"type": "file_end", "filename": filename})
                )
                await device_ws.send_text(
                    json.dumps(
                        {
                            "type": "file_checksum",
                            "filename": filename,
                            "sha256": hasher.hexdigest(),
                        }
                    )
                )
            except Exception as exc:
                logger.warning("[file/%s] could not finalize upload: %s", device_id, exc)

        # Stay open so the device's file_put_ok / file_put_error can be relayed back.
        try:
            while True:
                packet = await ws.receive()
                if packet.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass

    except WebSocketDisconnect:
        pass
    finally:
        if active_uploads.get(device_id) is ws:
            active_uploads.pop(device_id, None)


@router.websocket("/download/{device_id}")
async def file_download(ws: WebSocket, device_id: str):
    await ws.accept()

    device_ws = await _authorize(ws, device_id, "download")
    if device_ws is None:
        return

    try:
        try:
            req = json.loads(await ws.receive_text())
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"error": "Requisição inválida"}))
            await ws.close()
            return

        path = req.get("path")
        if not path:
            await ws.send_text(json.dumps({"error": "'path' é obrigatório"}))
            await ws.close()
            return

        active_downloads[device_id] = ws
        await device_ws.send_text(json.dumps({"type": "file_pull_begin", "path": path}))

        while True:
            packet = await ws.receive()
            if packet.get("type") == "websocket.disconnect":
                try:
                    await device_ws.send_text(json.dumps({"type": "file_pull_cancel"}))
                except Exception:
                    pass
                break
    except WebSocketDisconnect:
        pass
    finally:
        if active_downloads.get(device_id) is ws:
            active_downloads.pop(device_id, None)
