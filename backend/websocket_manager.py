import logging
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections with group and host channel isolation."""

    def __init__(self):
        self._connections: Dict[int, Dict[str, Set[WebSocket]]] = {}

    def _ensure(self, workshop_id: int, channel: str) -> Set[WebSocket]:
        if workshop_id not in self._connections:
            self._connections[workshop_id] = {}
        if channel not in self._connections[workshop_id]:
            self._connections[workshop_id][channel] = set()
        return self._connections[workshop_id][channel]

    async def connect(self, workshop_id: int, websocket: WebSocket, channel: str = "all"):
        await websocket.accept()
        conns = self._ensure(workshop_id, channel)
        conns.add(websocket)
        logger.info(f"WS connected: workshop={workshop_id}, channel={channel}")

    def disconnect(self, workshop_id: int, websocket: WebSocket):
        if workshop_id not in self._connections:
            return
        for ch in list(self._connections[workshop_id].keys()):
            self._connections[workshop_id][ch].discard(websocket)
        if not any(self._connections.get(workshop_id, {}).values()):
            self._connections.pop(workshop_id, None)

    async def _broadcast(self, workshop_id: int, channel: str, message: dict):
        stale = set()
        conns = self._connections.get(workshop_id, {}).get(channel, set())
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                stale.add(ws)
        for ws in stale:
            conns.discard(ws)

    async def broadcast_to_group(self, workshop_id: int, group_id: int, message: dict):
        await self._broadcast(workshop_id, str(group_id), message)

    async def broadcast_to_host(self, workshop_id: int, message: dict):
        await self._broadcast(workshop_id, "host", message)

    async def broadcast_to_all(self, workshop_id: int, message: dict):
        for ch in self._connections.get(workshop_id, {}):
            await self._broadcast(workshop_id, ch, message)

    async def broadcast_new_answer(self, workshop_id: int, group_id: int, answer_data: dict):
        msg = {"type": "new_answer", "data": answer_data}
        await self.broadcast_to_all(workshop_id, msg)

    async def broadcast_round_change(self, workshop_id: int, round_number: int, round_data: dict):
        msg = {"type": "round_changed", "data": {"round_number": round_number, "round": round_data}}
        await self.broadcast_to_all(workshop_id, msg)

    async def broadcast_result_ready(self, workshop_id: int, group_id: int, round_number: int, result_data: dict):
        msg = {"type": "result_ready", "data": {"round_number": round_number, "group_id": group_id, **result_data}}
        await self.broadcast_to_group(workshop_id, group_id, msg)
        await self.broadcast_to_host(workshop_id, msg)

    async def broadcast_ai_result_status(
        self,
        workshop_id: int,
        group_id: int,
        round_number: int,
        status: str,
        validation_error: Optional[str],
        updated_at: Optional[str],
    ):
        msg = {
            "type": "ai_result_status",
            "data": {
                "group_id": group_id,
                "round_number": round_number,
                "status": status,
                "validation_error": validation_error,
                "updated_at": updated_at,
            },
        }
        await self.broadcast_to_group(workshop_id, group_id, msg)
        await self.broadcast_to_host(workshop_id, msg)

    async def broadcast_group_leader_changed(
        self,
        workshop_id: int,
        group_id: int,
        members: List[dict],
        old_leader_participant_id: Optional[int] = None,
        old_leader_name: Optional[str] = None,
        new_leader_participant_id: Optional[int] = None,
        new_leader_name: Optional[str] = None,
        changed_by: str = "member",
    ):
        msg = {
            "type": "group_leader_changed",
            "data": {
                "group_id": group_id,
                "members": members,
                "old_leader_participant_id": old_leader_participant_id,
                "old_leader_name": old_leader_name,
                "new_leader_participant_id": new_leader_participant_id,
                "new_leader_name": new_leader_name,
                "changed_by": changed_by,
            },
        }
        await self.broadcast_to_group(workshop_id, group_id, msg)
        await self.broadcast_to_host(workshop_id, msg)

    async def broadcast_synthesis_ready(self, workshop_id: int, round_number: int, result_data: dict):
        msg = {"type": "synthesis_ready", "data": {"round_number": round_number, **result_data}}
        await self.broadcast_to_all(workshop_id, msg)

    async def broadcast_timer(
        self,
        workshop_id: int,
        seconds_remaining: int,
        phase: str,
        round_number: Optional[int] = None,
        round_id: Optional[int] = None,
    ):
        msg = {
            "type": "timer",
            "data": {
                "seconds_remaining": seconds_remaining,
                "phase": phase,
                "round_number": round_number,
                "round_id": round_id,
            },
        }
        await self.broadcast_to_all(workshop_id, msg)

    async def broadcast_workshop_completed(self, workshop_id: int):
        msg = {"type": "workshop_completed", "data": {"workshop_id": workshop_id}}
        await self.broadcast_to_all(workshop_id, msg)
