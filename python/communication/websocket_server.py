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
        try:
            async for _ in ws:
                pass  # 클라이언트에서 오는 메시지는 무시 (단방향 전송)
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
    ):
        """
        매 프레임 관절 데이터를 Unity로 전송한다.

        Args:
            joints:       {"leg_R1_base": {"x": 0.0, "y": 45.3, "z": 0.0}, ...}
            animal:       현재 동물 이름 ("spider" 등)
            hand_detected: 손이 감지됐는지 여부
            gesture:      감지된 제스처 이름 또는 None
        """
        payload = {
            "type":         "frame",
            "animal":       animal,
            "joints":       joints,
            "hand_detected": hand_detected,
            "gesture":      gesture,
        }
        self._broadcast(json.dumps(payload))

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
