from typing import Dict
import asyncio
from datetime import datetime
from fastapi import WebSocket

# Centralized state storage for connected devices and file transfer sessions
connected_clients: Dict[str, WebSocket] = {}
device_queues: Dict[str, Dict[str, asyncio.Queue]] = {}
active_downloads: Dict[str, WebSocket] = {}
active_uploads: Dict[str, WebSocket] = {}
connection_times: Dict[str, datetime] = {}
