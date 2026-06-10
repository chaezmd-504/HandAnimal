"""
websocket_server.py
--------------------
asyncio + websockets 기반 WebSocket 서버.
Python 파이프라인에서 계산한 관절 데이터를 Unity로 실시간 전송한다.

사용 예:
    server = WebSocketServer(host="localhost", port=8765)
    server.start()                          # 백그라운드 스레드에서 이벤트 루프 시작
    server.send_frame(joints, animal, hand_detected, gesture)
    server.send_switch(animal_name)
    server.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765


class WebSocketServer:
    """
    비동기 WebSocket 서버.
    별도 스레드에서 asyncio 이벤트 루프를 실행하고,
    연결된 모든 Unity 클라이언트에 데이터를 브로드캐스트한다.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port

        self._clients: set[WebSocketServerProtocol] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._on_message = None   # Callable[[str], None] | None
        self._on_connect  = None  # Callable[[], None]   | None

    @property
    def on_message(self):
        return self._on_message

    @on_message.setter
    def on_message(self, callback):
        self._on_message = callback

    @property
    def on_connect(self):
        return self._on_connect

    @on_connect.setter
    def on_connect(self, callback):
        self._on_connect = callback

    # ──────────────────────────────────────────────────────────
    # 서버 생명주기
    # ──────────────────────────────────────────────────────────

    def start(self):
        """백그라운드 스레드에서 WebSocket 서버를 시작한다."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[WebSocketServer] 서버 시작: ws://{self.host}:{self.port}")

    def stop(self):
        """서버를 종료한다."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=3.0)
        print("[WebSocketServer] 서버 종료.")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.close()

    async def _serve(self):
        self._stop_event = asyncio.Event()
        async with websockets.serve(self._handler, self.host, self.port):
            await self._stop_event.wait()

    # ──────────────────────────────────────────────────────────
    # 클라이언트 연결 핸들러
    # ──────────────────────────────────────────────────────────

    async def _handler(self, ws: WebSocketServerProtocol):
        self._clients.add(ws)
        addr = ws.remote_address
        print(f"[WebSocketServer] Unity 연결됨: {addr}")
        if self._on_connect is not None:
            self._on_connect()
        try:
            async for msg in ws:
                if self._on_message is not None:
                    self._on_message(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            print(f"[WebSocketServer] Unity 연결 해제: {addr}")

    # ──────────────────────────────────────────────────────────
    # 외부 호출 API
    # ──────────────────────────────────────────────────────────

    def _broadcast(self, message: str):
        """현재 연결된 모든 클라이언트에 메시지를 전송한다."""
        if not self._loop or not self._clients:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(message), self._loop
        )

    async def _async_broadcast(self, message: str):
        if not self._clients:
            return
        # 끊긴 클라이언트는 제외하고 전송
        dead: set[WebSocketServerProtocol] = set()
        for ws in list(self._clients):
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self._clients -= dead

    def send_frame(
        self,
        joints: dict[str, dict],
        animal: str,
        hand_detected: bool,
        gesture: Optional[str] = None,
        locomotion: Optional[dict] = None,
    ):
        """
        매 프레임 관절 데이터를 Unity로 전송한다.

        Args:
            joints:       {"leg_R1_base": {"x": 0.0, "y": 45.3, "z": 0.0}, ...}
            animal:       현재 동물 이름 ("spider" 등)
            hand_detected: 손이 감지됐는지 여부
            gesture:      감지된 제스처 이름 또는 None
            locomotion:   이동 파라미터 dict 또는 None
                          {"speed": float, "yaw_delta": float, "cursor": float, "valid": bool}
        """
        payload = {
            "type":          "frame",
            "animal":        animal,
            "joints":        joints,
            "hand_detected": hand_detected,
            "gesture":       gesture,
        }
        if locomotion is not None:
            payload["locomotion"] = locomotion
        self._broadcast(json.dumps(payload))

    def send_trigger(self, anim: str, duration: float = 2.0):
        """
        트리거 이벤트를 Unity로 전송한다.
        Unity는 해당 Animator 상태를 재생하고 duration 동안 관절 데이터를 무시한다.

        Args:
            anim:     재생할 애니메이션 이름 ("Attack1", "Attack2", "Death" 등)
            duration: 애니메이션 지속 시간 (초) — Unity 타이머에 전달
        """
        payload = {
            "type":     "trigger",
            "anim":     anim,
            "duration": round(duration, 2),
        }
        self._broadcast(json.dumps(payload))
        print(f"[WebSocketServer] 트리거 전송: {anim} ({duration:.1f}s)")

    def send_switch(self, animal: str):
        """
        동물 전환 메시지를 Unity로 전송한다.

        Args:
            animal: 전환할 동물 이름
        """
        payload = {
            "type":   "switch_animal",
            "animal": animal,
        }
        self._broadcast(json.dumps(payload))
        print(f"[WebSocketServer] 동물 전환 전송: {animal}")

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ──────────────────────────────────────────────────────────────
# 단독 실행 테스트 (브라우저 DevTools 또는 wscat으로 수신 확인)
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.WARNING)

    server = WebSocketServer()
    server.start()

    print("[TEST] 테스트 데이터 전송 중... Ctrl+C 로 종료")
    print("[TEST] wscat -c ws://localhost:8765 또는 브라우저 콘솔로 수신 확인")

    angle = 0.0
    try:
        while True:
            joints = {
                "leg_R1_base": round(angle % 60, 2),
                "leg_R1_mid":  round((angle * 1.2) % 90, 2),
                "leg_L1_base": round((angle * 0.8) % 60, 2),
            }
            server.send_frame(joints, "spider", hand_detected=True)
            angle += 2.0
            time.sleep(1 / 60)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
