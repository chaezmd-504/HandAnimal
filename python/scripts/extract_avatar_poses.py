"""
extract_avatar_poses.py
------------------------
Unity 애니메이션 키프레임에서 아바타 포즈 P를 추출하여
data/animal_skeletons/{animal}_poses.json 으로 저장한다.

★ 사용 방법 (두 가지)
  방법 A — 자동 추출 (Unity AvatarPoseSender 연동):
      1) 이 스크립트를 --mode ws 로 실행 (포트 8766 WebSocket 서버 시작)
      2) Unity 씬에서 AvatarPoseSender 컴포넌트 → Inspector에서 "연결" 클릭
      3) 원하는 포즈로 아바타를 배치한 뒤 "현재 포즈 캡처" 클릭 (반복)
      4) "저장 (현재 동물)" 또는 "저장 (전체 동물)" 클릭 → JSON 파일 생성

      실행 예:
          conda activate capstone_env
          python scripts/extract_avatar_poses.py --mode ws [--port 8766]

  방법 B — 수동 입력:
      아래 MANUAL_POSES 딕셔너리에 직접 각도 값을 입력한 뒤 실행.
      (Unity 애니메이션 키프레임 창에서 값을 읽어 입력)

      실행 예:
          conda activate capstone_env
          python scripts/extract_avatar_poses.py [--animal spider]

포즈 선택 기준 (논문 및 설계/아키텍쳐.md 참고):
  - 정지 기본 포즈 (모든 관절 0° 혹은 휴식)
  - 최대 굴신 포즈 (대표 관절 최대각)
  - 보행/동작 중간 자세 (실제 사용 빈도 높음)
  - 특징적 자세 (앞다리 들기, 날개 완전 펼침 등)
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

WS_HOST = "localhost"
WS_PORT = 8766

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
OUT_DIR    = os.path.join(PYTHON_DIR, "data", "animal_skeletons")

# ──────────────────────────────────────────────────────────────
# 수동 입력 포즈 (Unity 키프레임 값으로 교체할 것)
# ⚠️ 아래 값은 Unity 실측 전 초기값. 반드시 실제 모델에서 검증 필요.
# ──────────────────────────────────────────────────────────────
MANUAL_POSES: dict[str, list[dict]] = {

    # ── 거미 (8다리, 각 다리 base/mid/tip) ──────────────────
    "spider": [
        # P1: 정지 자세 (모든 다리 펼침)
        {
            "leg_R1_base": 0.0,  "leg_R1_mid": 0.0,  "leg_R1_tip": 0.0,
            "leg_R2_base": 0.0,  "leg_R2_mid": 0.0,  "leg_R2_tip": 0.0,
            "leg_R3_base": 0.0,  "leg_R3_mid": 0.0,  "leg_R3_tip": 0.0,
            "leg_R4_base": 0.0,  "leg_R4_mid": 0.0,  "leg_R4_tip": 0.0,
            "leg_L1_base": 0.0,  "leg_L1_mid": 0.0,  "leg_L1_tip": 0.0,
            "leg_L2_base": 0.0,  "leg_L2_mid": 0.0,  "leg_L2_tip": 0.0,
            "leg_L3_base": 0.0,  "leg_L3_mid": 0.0,  "leg_L3_tip": 0.0,
            "leg_L4_base": 0.0,  "leg_L4_mid": 0.0,  "leg_L4_tip": 0.0,
        },
        # P2: 보행 자세 1 (오른쪽 다리 들기, 왼쪽 내리기)
        {
            "leg_R1_base": 20.0, "leg_R1_mid": 60.0, "leg_R1_tip": 40.0,
            "leg_R2_base":  0.0, "leg_R2_mid": 45.0, "leg_R2_tip": 30.0,
            "leg_R3_base": 20.0, "leg_R3_mid": 60.0, "leg_R3_tip": 40.0,
            "leg_R4_base":  0.0, "leg_R4_mid": 45.0, "leg_R4_tip": 30.0,
            "leg_L1_base":  0.0, "leg_L1_mid": 45.0, "leg_L1_tip": 30.0,
            "leg_L2_base": 20.0, "leg_L2_mid": 60.0, "leg_L2_tip": 40.0,
            "leg_L3_base":  0.0, "leg_L3_mid": 45.0, "leg_L3_tip": 30.0,
            "leg_L4_base": 20.0, "leg_L4_mid": 60.0, "leg_L4_tip": 40.0,
        },
        # P3: 보행 자세 2 (반대 교번)
        {
            "leg_R1_base":  0.0, "leg_R1_mid": 45.0, "leg_R1_tip": 30.0,
            "leg_R2_base": 20.0, "leg_R2_mid": 60.0, "leg_R2_tip": 40.0,
            "leg_R3_base":  0.0, "leg_R3_mid": 45.0, "leg_R3_tip": 30.0,
            "leg_R4_base": 20.0, "leg_R4_mid": 60.0, "leg_R4_tip": 40.0,
            "leg_L1_base": 20.0, "leg_L1_mid": 60.0, "leg_L1_tip": 40.0,
            "leg_L2_base":  0.0, "leg_L2_mid": 45.0, "leg_L2_tip": 30.0,
            "leg_L3_base": 20.0, "leg_L3_mid": 60.0, "leg_L3_tip": 40.0,
            "leg_L4_base":  0.0, "leg_L4_mid": 45.0, "leg_L4_tip": 30.0,
        },
        # P4: 완전 굴신 (다리 완전히 구부림)
        {
            "leg_R1_base": 30.0, "leg_R1_mid": 100.0, "leg_R1_tip": 70.0,
            "leg_R2_base": 30.0, "leg_R2_mid": 100.0, "leg_R2_tip": 70.0,
            "leg_R3_base": 30.0, "leg_R3_mid": 100.0, "leg_R3_tip": 70.0,
            "leg_R4_base": 30.0, "leg_R4_mid": 100.0, "leg_R4_tip": 70.0,
            "leg_L1_base": 30.0, "leg_L1_mid": 100.0, "leg_L1_tip": 70.0,
            "leg_L2_base": 30.0, "leg_L2_mid": 100.0, "leg_L2_tip": 70.0,
            "leg_L3_base": 30.0, "leg_L3_mid": 100.0, "leg_L3_tip": 70.0,
            "leg_L4_base": 30.0, "leg_L4_mid": 100.0, "leg_L4_tip": 70.0,
        },
        # P5: 앞다리 들기 (R1/L1 들기)
        {
            "leg_R1_base": 50.0, "leg_R1_mid": 80.0, "leg_R1_tip": 50.0,
            "leg_R2_base":  0.0, "leg_R2_mid": 30.0, "leg_R2_tip": 20.0,
            "leg_R3_base": -10.0,"leg_R3_mid": 30.0, "leg_R3_tip": 20.0,
            "leg_R4_base": -20.0,"leg_R4_mid": 30.0, "leg_R4_tip": 20.0,
            "leg_L1_base": -50.0,"leg_L1_mid": 80.0, "leg_L1_tip": 50.0,
            "leg_L2_base":  0.0, "leg_L2_mid": 30.0, "leg_L2_tip": 20.0,
            "leg_L3_base": 10.0, "leg_L3_mid": 30.0, "leg_L3_tip": 20.0,
            "leg_L4_base": 20.0, "leg_L4_mid": 30.0, "leg_L4_tip": 20.0,
        },
    ],

    # ── 나비 (좌우 날개 inner/outer) ─────────────────────────
    "butterfly": [
        # P1: 날개 완전 접힘
        {"wing_R_inner": -15.0, "wing_R_outer": -5.0,
         "wing_L_inner":  15.0, "wing_L_outer":  5.0},
        # P2: 날개 반쯤 펼침
        {"wing_R_inner":  35.0, "wing_R_outer": 17.0,
         "wing_L_inner": -35.0, "wing_L_outer":-17.0},
        # P3: 날개 완전 펼침 (최대)
        {"wing_R_inner":  85.0, "wing_R_outer": 40.0,
         "wing_L_inner": -85.0, "wing_L_outer":-40.0},
        # P4: 날갯짓 위로
        {"wing_R_inner":  70.0, "wing_R_outer": 30.0,
         "wing_L_inner": -70.0, "wing_L_outer":-30.0},
        # P5: 날갯짓 아래로
        {"wing_R_inner":  10.0, "wing_R_outer":  5.0,
         "wing_L_inner": -10.0, "wing_L_outer": -5.0},
    ],

    # ── 물고기 (몸통 굴곡 + 지느러미) ───────────────────────
    "fish": [
        # P1: 직진 (모두 0)
        {"body_mid": 0.0, "body_tail": 0.0,
         "fin_pectoral_R": 0.0, "fin_pectoral_L": 0.0, "fin_dorsal": 0.0},
        # P2: 왼쪽 회전
        {"body_mid": -30.0, "body_tail": -45.0,
         "fin_pectoral_R":  10.0, "fin_pectoral_L": -20.0, "fin_dorsal": 0.0},
        # P3: 오른쪽 회전
        {"body_mid":  30.0, "body_tail":  45.0,
         "fin_pectoral_R": -10.0, "fin_pectoral_L":  20.0, "fin_dorsal": 0.0},
        # P4: 유영 웨이브 1
        {"body_mid":  20.0, "body_tail": -30.0,
         "fin_pectoral_R":  5.0, "fin_pectoral_L": -5.0, "fin_dorsal": 10.0},
        # P5: 유영 웨이브 2
        {"body_mid": -20.0, "body_tail":  30.0,
         "fin_pectoral_R": -5.0, "fin_pectoral_L":  5.0, "fin_dorsal":-10.0},
    ],

    # ── 문어 (8촉수, 각 촉수 base/mid/tip) ──────────────────
    "octopus": [
        # P1: 휴식 (모두 0)
        {f"tentacle_{t}_{s}": 0.0
         for t in range(1, 9) for s in ("base", "mid", "tip")},
        # P2: 팔 펼치기
        {f"tentacle_{t}_{s}": v
         for t in range(1, 9)
         for s, v in [("base", 20.0), ("mid", 40.0), ("tip", 30.0)]},
        # P3: 팔 구부리기 (잡기 동작)
        {f"tentacle_{t}_{s}": v
         for t in range(1, 9)
         for s, v in [("base", 45.0), ("mid", 80.0), ("tip", 70.0)]},
        # P4: 교번 패턴 1 (홀수 들기, 짝수 내리기)
        {f"tentacle_{t}_{s}": (v_up if t % 2 == 1 else v_dn)
         for t in range(1, 9)
         for s, v_up, v_dn in [("base", 30.0, 0.0), ("mid", 60.0, 20.0), ("tip", 45.0, 10.0)]},
        # P5: 수축 (모두 최대 굴신)
        {f"tentacle_{t}_{s}": v
         for t in range(1, 9)
         for s, v in [("base", 60.0), ("mid", 90.0), ("tip", 80.0)]},
    ],

    # ── 뱀 (척추 8마디) ──────────────────────────────────────
    "snake": [
        # P1: 일직선
        {f"spine_{i}": 0.0 for i in range(1, 9)},
        # P2: S자 웨이브 1
        {f"spine_{i}": (30.0 if i % 2 == 1 else -30.0) for i in range(1, 9)},
        # P3: S자 웨이브 2 (반전)
        {f"spine_{i}": (-30.0 if i % 2 == 1 else 30.0) for i in range(1, 9)},
        # P4: 완전 코일 (한 방향)
        {f"spine_{i}": 40.0 for i in range(1, 9)},
        # P5: 완만한 웨이브
        {f"spine_{i}": (15.0 if i % 2 == 1 else -15.0) for i in range(1, 9)},
    ],
}


def save_poses(animal: str, poses: list[dict]):
    out_path = os.path.join(OUT_DIR, f"{animal}_poses.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(poses, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(poses)}개 포즈 저장: {out_path}")


def validate_poses(animal: str, poses: list[dict]):
    """각 포즈가 같은 관절 키를 가지는지 확인."""
    if not poses:
        print(f"[WARN] {animal}: 포즈가 비어 있습니다.")
        return
    keys_ref = set(poses[0].keys())
    for i, p in enumerate(poses[1:], 1):
        if set(p.keys()) != keys_ref:
            diff = keys_ref.symmetric_difference(p.keys())
            print(f"[WARN] {animal} 포즈 {i}: 관절 키 불일치 — {diff}")
    print(f"  [검증] {animal}: {len(poses)}개 포즈, {len(keys_ref)}개 관절 OK")


# ──────────────────────────────────────────────────────────────
# 방법 A: WebSocket 수신 서버 (Unity AvatarPoseSender 연동)
# ──────────────────────────────────────────────────────────────

async def _ws_serve(host: str, port: int):
    """Unity로부터 포즈 데이터를 수신하는 WebSocket 서버."""
    collected: dict[str, list[dict]] = defaultdict(list)

    async def handler(websocket):
        addr = websocket.remote_address
        print(f"[WS] Unity 연결됨: {addr}")
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                animal   = data.get("animal", "unknown")

                if msg_type == "capture_pose":
                    joints = data.get("joints", {})
                    idx = len(collected[animal])
                    collected[animal].append(joints)
                    print(f"  [{animal}] 포즈 #{idx + 1} 캡처  ({len(joints)}개 관절)")
                    await websocket.send(json.dumps({"status": "ok", "index": idx}))

                elif msg_type == "save":
                    poses = collected.get(animal, [])
                    if poses:
                        validate_poses(animal, poses)
                        save_poses(animal, poses)
                    else:
                        print(f"[WARN] {animal}: 저장할 포즈 없음.")
                    await websocket.send(json.dumps({
                        "status": "saved", "animal": animal, "count": len(poses)
                    }))

                elif msg_type == "save_all":
                    for a, poses in collected.items():
                        if poses:
                            validate_poses(a, poses)
                            save_poses(a, poses)
                    await websocket.send(json.dumps({
                        "status": "saved_all", "animals": list(collected.keys())
                    }))
                    print(f"\n[완료] {OUT_DIR} 에 저장됨.")

                elif msg_type == "clear":
                    collected[animal].clear()
                    print(f"  [{animal}] 포즈 목록 초기화")
                    await websocket.send(json.dumps({"status": "cleared"}))

                elif msg_type == "status":
                    summary = {a: len(p) for a, p in collected.items()}
                    await websocket.send(json.dumps({"status": "ok", "captured": summary}))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            print(f"[WS] Unity 연결 해제: {addr}")

    print(f"[WS] 포즈 캡처 서버 시작 → ws://{host}:{port}")
    print("[WS] Unity AvatarPoseSender에서 위 주소로 연결하세요.")
    print("[WS] Ctrl+C 로 종료\n")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()  # 종료 신호가 올 때까지 대기


def ws_mode(host: str = WS_HOST, port: int = WS_PORT):
    if not _WS_AVAILABLE:
        print("[ERROR] websockets 패키지 없음: pip install websockets")
        sys.exit(1)
    try:
        asyncio.run(_ws_serve(host, port))
    except KeyboardInterrupt:
        print("\n[WS] 서버 종료.")


# ──────────────────────────────────────────────────────────────
# 방법 B: 수동 입력 (기존 동작)
# ──────────────────────────────────────────────────────────────

def manual_mode(animal: str):
    animals = list(MANUAL_POSES.keys()) if animal == "all" else [animal]
    for a in animals:
        poses = MANUAL_POSES[a]
        validate_poses(a, poses)
        save_poses(a, poses)
    print(f"\n[완료] {OUT_DIR} 에 저장됨.")
    print("[주의] 이 값들은 Unity 실측 전 초기값입니다.")
    print("       Unity 애니메이션 키프레임에서 실제 각도 확인 후 교체를 권장합니다.")


def main():
    parser = argparse.ArgumentParser(
        description="아바타 포즈 P 추출",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  방법 A (Unity 자동): python scripts/extract_avatar_poses.py --mode ws\n"
            "  방법 B (수동 입력): python scripts/extract_avatar_poses.py --animal spider"
        ),
    )
    parser.add_argument(
        "--mode", choices=["manual", "ws"], default="manual",
        help="manual: 수동 입력 (방법 B) / ws: Unity WebSocket 수신 (방법 A)",
    )
    parser.add_argument(
        "--animal", choices=list(MANUAL_POSES.keys()) + ["all"], default="all",
        help="[manual 모드] 추출할 동물 (기본: all)",
    )
    parser.add_argument(
        "--port", type=int, default=WS_PORT,
        help=f"[ws 모드] WebSocket 포트 (기본: {WS_PORT})",
    )
    parser.add_argument(
        "--host", default=WS_HOST,
        help=f"[ws 모드] WebSocket 호스트 (기본: {WS_HOST})",
    )
    args = parser.parse_args()

    if args.mode == "ws":
        ws_mode(args.host, args.port)
    else:
        manual_mode(args.animal)


if __name__ == "__main__":
    main()
