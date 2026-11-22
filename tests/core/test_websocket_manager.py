"""Tests for WebSocket room scoping and SID room synchronization."""

import threading

from shelfmark.api import websocket as websocket_module
from shelfmark.api.websocket import WebSocketManager


def test_join_user_room_without_db_user_id_does_not_join_admin_room(monkeypatch):
    joined: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        websocket_module,
        "join_room",
        lambda room, sid=None: joined.append((room, sid)),
    )

    manager = WebSocketManager()
    manager.join_user_room("sid-1", is_admin=False, db_user_id=None)

    assert joined == []
    assert manager._user_rooms == {}


def test_room_helpers_accept_positional_scope_args(monkeypatch):
    joined: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        websocket_module,
        "join_room",
        lambda room, sid=None: joined.append((room, sid)),
    )

    manager = WebSocketManager()
    manager.join_user_room("sid-1", False, 7)
    manager.sync_user_room("sid-2", True, None)

    assert joined == [("user_7", "sid-1"), ("admins", "sid-2")]


def test_sync_user_room_moves_sid_between_rooms(monkeypatch):
    joined: list[tuple[str, str | None]] = []
    left: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        websocket_module,
        "join_room",
        lambda room, sid=None: joined.append((room, sid)),
    )
    monkeypatch.setattr(
        websocket_module,
        "leave_room",
        lambda room, sid=None: left.append((room, sid)),
    )

    manager = WebSocketManager()

    manager.sync_user_room("sid-1", is_admin=True, db_user_id=None)
    assert joined == [("admins", "sid-1")]
    assert left == []
    assert manager._user_rooms == {}

    manager.sync_user_room("sid-1", is_admin=False, db_user_id=42)
    assert joined[-1] == ("user_42", "sid-1")
    assert left[-1] == ("admins", "sid-1")
    assert manager._user_rooms == {"user_42": 1}

    manager.leave_user_room("sid-1")
    assert left[-1] == ("user_42", "sid-1")
    assert manager._user_rooms == {}


def test_sync_user_room_tracks_ref_counts_across_multiple_sids(monkeypatch):
    monkeypatch.setattr(websocket_module, "join_room", lambda *_, **__: None)
    monkeypatch.setattr(websocket_module, "leave_room", lambda *_, **__: None)

    manager = WebSocketManager()

    manager.sync_user_room("sid-a", is_admin=False, db_user_id=7)
    manager.sync_user_room("sid-b", is_admin=False, db_user_id=7)
    assert manager._user_rooms == {"user_7": 2}

    manager.sync_user_room("sid-a", is_admin=False, db_user_id=8)
    assert manager._user_rooms == {"user_7": 1, "user_8": 1}

    manager.leave_user_room("sid-a")
    manager.leave_user_room("sid-b")
    assert manager._user_rooms == {}


def test_progress_broadcast_survives_a_client_disconnecting_during_emit(monkeypatch):
    monkeypatch.setattr(websocket_module, "join_room", lambda *_, **__: None)
    monkeypatch.setattr(websocket_module, "leave_room", lambda *_, **__: None)

    manager = WebSocketManager()
    manager._enabled = True
    manager.sync_user_room("sid-1", is_admin=False, db_user_id=7)

    class DisconnectingSocketIO:
        """Closes the client from inside emit(), as a ping timeout does."""

        def emit(self, _event, _data, to=None):
            if to == "user_7":
                manager.leave_user_room("sid-1")

    manager.socketio = DisconnectingSocketIO()

    finished = threading.Event()

    def broadcast():
        manager.broadcast_download_progress("book-1", 50.0, "downloading", user_id=7)
        finished.set()

    threading.Thread(target=broadcast, daemon=True).start()

    assert finished.wait(timeout=2), "broadcast deadlocked on the room lock"
    assert manager._user_rooms == {}
