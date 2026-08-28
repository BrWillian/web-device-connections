import asyncio
import json
import logging

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect, status

from app.core import presence as presence_module
from app.core.config import settings
from app.core.security import (
    DeviceAuthUnavailable,
    mint_device_token,
    verify_device_token,
    verify_relay_secret,
)

from ..state import (
    active_downloads,
    active_uploads,
    connected_clients,
    device_queues,
)

logger = logging.getLogger(__name__)
router = APIRouter()

WS_UNAUTHORIZED = 4401
WS_REVOKED = 4403
AUTH_TIMEOUT_SECONDS = 10
REVOCATION_POLL_SECONDS = 15


@router.get("/devices")
async def list_devices(x_relay_secret: str = Header(default="")):
    """The whole fleet, not just the devices attached to this replica.

    Called by the frontend server-to-server, never by a browser, so it is guarded
    by the shared secret rather than by a user session. Reading from the shared
    presence store is what lets any replica answer for the whole fleet.
    """
    if not verify_relay_secret(x_relay_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="segredo do relay inválido"
        )
    return await presence_module.presence.list_devices()


@router.get("/devices/{device_id}/token")
async def device_token(device_id: str, x_relay_secret: str = Header(default="")):
    """Derive the token to provision on a device.

    Exists so DEVICE_MASTER_SECRET can stay in this one process. The frontend has
    to show an operator the token to put on a device, but giving it the secret that
    every token derives from would put the same key in two apps.

    Guarded by the shared secret, like /devices. That is not a widening: anyone
    holding RELAY_SECRET can already sign a grant for any device and open a shell
    on it, which is strictly worse than reading a device's token.
    """
    if not verify_relay_secret(x_relay_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="segredo do relay inválido"
        )
    try:
        return {"device_id": device_id, "token": mint_device_token(device_id)}
    except DeviceAuthUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEVICE_MASTER_SECRET não configurado no relay",
        )


async def _relay_device_messages(ws: WebSocket, device_id: str) -> None:
    """Pump everything the device sends toward whoever is waiting for it."""
    while True:
        packet = await ws.receive()
        if packet.get("type") == "websocket.disconnect":
            break

        text = packet.get("text")
        if text is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("[%s] non-JSON text frame ignored", device_id)
                continue

            msg_type = data.get("type")
            if msg_type == "output":
                queue = device_queues.get(device_id, {}).get(data.get("session_id"))
                if queue:
                    await queue.put(text)
            elif msg_type in ("file_pull_info", "file_pull_checksum", "file_pull_end", "file_pull_error"):
                await _forward(active_downloads.get(device_id), text)
            elif msg_type in ("file_put_ok", "file_put_error", "file_put_progress"):
                await _forward(active_uploads.get(device_id), text)
            continue

        chunk = packet.get("bytes")
        if chunk is not None:
            dl_ws = active_downloads.get(device_id)
            if dl_ws is not None:
                try:
                    await dl_ws.send_bytes(chunk)
                except Exception:
                    pass  # the browser hung up; the download handler cleans up


async def _forward(target: WebSocket | None, text: str) -> None:
    if target is None:
        return
    try:
        await target.send_text(text)
    except Exception:
        pass


async def _revocation_watch(ws: WebSocket, device_id: str) -> None:
    """Drop a device that is revoked while it is already connected.

    Checking only at connect time would leave a revoked device with a live shell
    until it happened to reconnect, which for a long-lived agent could be days.
    """
    try:
        while True:
            await asyncio.sleep(REVOCATION_POLL_SECONDS)
            if await presence_module.presence.is_revoked(device_id):
                logger.warning("device %s revoked while connected; closing", device_id)
                try:
                    await ws.send_text(
                        json.dumps({"type": "auth_error", "error": "dispositivo revogado"})
                    )
                    await ws.close(code=WS_REVOKED)
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a check failing must not kill the session
        logger.warning("revocation watch stopped for %s: %s", device_id, exc)


async def _serve_device(ws: WebSocket, device_id: str) -> None:
    """Own the device socket for the lifetime of the connection."""
    previous = connected_clients.get(device_id)
    if previous is not None:
        # A reconnect after an unclean drop: the stale socket must go, otherwise
        # output would be relayed to a half-dead connection.
        logger.info("device %s reconnected; closing stale socket", device_id)
        try:
            await previous.close()
        except Exception:
            pass

    connected_clients[device_id] = ws
    device_queues[device_id] = {}
    await presence_module.presence.register(device_id, settings.pod_name)
    heartbeat = asyncio.create_task(presence_module.heartbeat_loop(device_id))
    revocation = asyncio.create_task(_revocation_watch(ws, device_id))
    logger.info("device %s connected to pod %s", device_id, settings.pod_name)

    try:
        await _relay_device_messages(ws, device_id)
    except WebSocketDisconnect:
        pass
    finally:
        # Cancel first, unregister second, reap last. cancel() is synchronous, so
        # neither task can run again after this line — which matters for the
        # heartbeat, since a refresh landing after the unregister would put the
        # device straight back into the presence store.
        heartbeat.cancel()
        revocation.cancel()

        if connected_clients.get(device_id) is ws:
            connected_clients.pop(device_id, None)
            device_queues.pop(device_id, None)
            await presence_module.presence.unregister(device_id)
            logger.info("device %s disconnected", device_id)

        # Reaping is only housekeeping — it keeps asyncio from warning about
        # pending tasks — so it happens after the state the fleet can observe has
        # already been put right.
        for task in (heartbeat, revocation):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        for registry in (active_downloads, active_uploads):
            peer = registry.pop(device_id, None)
            if peer is not None:
                try:
                    await peer.close()
                except Exception:
                    pass


@router.websocket("/device/{device_id}")
async def device_connect(ws: WebSocket, device_id: str):
    """Authenticated device endpoint.

    The device id sits in the path so the ingress can hash on it and land this
    connection on the same pod as the browser sessions for the same device.
    """
    await ws.accept()

    try:
        # Bounded: a socket that connects and never identifies itself would
        # otherwise sit open indefinitely, one file descriptor at a time.
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("device %s did not send credentials in time", device_id)
        await ws.close(code=WS_UNAUTHORIZED)
        return
    except WebSocketDisconnect:
        return

    try:
        hello = json.loads(raw)
    except json.JSONDecodeError:
        hello = {}

    if not verify_device_token(device_id, hello.get("token")):
        logger.warning("rejected device %s: invalid or missing token", device_id)
        await ws.send_text(json.dumps({"type": "auth_error", "error": "token inválido"}))
        await ws.close(code=WS_UNAUTHORIZED)
        return

    # Checked after the token, so an unauthenticated caller cannot probe which
    # device ids are revoked and therefore which ones exist.
    if await presence_module.presence.is_revoked(device_id):
        logger.warning("rejected device %s: revoked", device_id)
        await ws.send_text(
            json.dumps({"type": "auth_error", "error": "dispositivo revogado"})
        )
        await ws.close(code=WS_REVOKED)
        return

    await ws.send_text(json.dumps({"type": "auth_ok", "pod": settings.pod_name}))
    await _serve_device(ws, device_id)


@router.websocket("/device")
async def device_connect_legacy(ws: WebSocket):
    """Unauthenticated pre-migration endpoint, off unless ALLOW_LEGACY_DEVICES=true.

    It cannot be hashed by the ingress (no device id in the path), so a fleet still
    using it will not route correctly across replicas. Keep it on only long enough
    to roll the new client out to every device.
    """
    await ws.accept()

    if not settings.allow_legacy_devices:
        await ws.send_text(
            json.dumps({"type": "auth_error", "error": "use /device/{device_id} com token"})
        )
        await ws.close(code=WS_UNAUTHORIZED)
        return

    try:
        device_id = await ws.receive_text()
    except WebSocketDisconnect:
        return

    logger.warning("device %s connected over the legacy unauthenticated route", device_id)
    await _serve_device(ws, device_id)
