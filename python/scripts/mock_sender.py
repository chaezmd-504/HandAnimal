"""
mock_sender.py — 카메라·MediaPipe만 교체한 테스트 전송기
=========================================================
카메라와 MediaPipe를 가짜 DOF 각도 생성기로 대체하고,
매핑 엔진(KeyframeMappingEngine / MappingEngine)과
WebSocket 서버는 실제 코드를 그대로 사용한다.

파이프라인:
    [가짜 hands_angles] → mapping engine → WebSocket → Unity
    (실제와 완전히 동일한 경로, 카메라/MediaPipe만 없음)

실행 예:
    python scripts/mock_sender.py --animal spider
    python scripts/mock_sender.py --animal horse --mode wave
    python scripts/mock_sender.py --animal spider --steer 12
    python scripts/mock_sender.py --animal spider --trigger Attack1

모드:
    idle   손 펼침 고정 (매핑 결과 확인용)
    walk   손가락 순차 굴신 반복 (걷기 유발용)
    wave   전체 DOF 천천히 사인파 (ROM·축 확인용)
    wrist  wrist_flex/dev 위주 사인파 (horse 머리·척추 확인용)
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from communication.websocket_server import WebSocketServer

# ── 경로 ─────────────────────────────────────────────────────────────────────

MAPPINGS_DIR = str(ROOT / "data" / "mappings")
POSES_DIR    = str(ROOT / "data" / "animal_skeletons")

# ── DOF 정의 (compute_dof_angles 반환값과 동일한 키) ──────────────────────────

#  중립 포즈: 캘리브레이션용 (손을 쫙 편 상태)
NEUTRAL: dict[str, float] = {
    "wrist_flex":  0.0,
    "wrist_dev":   0.0,
    "wrist_rot":   0.0,
    "thumb_cmc":   5.0,
    "thumb_abd":  10.0,
    "thumb_mcp":   5.0,
    "thumb_ip":    3.0,
    "index_mcp":   5.0,  "index_pip":   5.0,  "index_dip":   3.0,
    "middle_mcp":  5.0,  "middle_pip":  5.0,  "middle_dip":  3.0,
    "ring_mcp":    5.0,  "ring_pip":    5.0,  "ring_dip":    3.0,
    "pinky_cmc":   2.0,
    "pinky_mcp":   5.0,  "pinky_pip":   5.0,  "pinky_dip":   3.0,
}

# DOF별 최대 굴신 범위 (wave 모드용)
DOF_RANGES: dict[str, tuple[float, float]] = {
    "wrist_flex":  (-60, 60),
    "wrist_dev":   (-20, 20),
    "wrist_rot":   (-60, 60),
    "thumb_cmc":   (0, 50),
    "thumb_abd":   (0, 60),
    "thumb_mcp":   (0, 50),
    "thumb_ip":    (0, 70),
    "index_mcp":   (0, 80),   "index_pip":   (0, 100),  "index_dip":   (0, 80),
    "middle_mcp":  (0, 80),   "middle_pip":  (0, 100),  "middle_dip":  (0, 80),
    "ring_mcp":    (0, 80),   "ring_pip":    (0, 100),  "ring_dip":    (0, 80),
    "pinky_cmc":   (0, 35),
    "pinky_mcp":   (0, 80),   "pinky_pip":   (0, 100),  "pinky_dip":   (0, 80),
}

TAU = 2 * math.pi

# ── DOF 생성 함수 ─────────────────────────────────────────────────────────────

def _sin01(t: float, freq: float, phase: float = 0.0) -> float:
    """0~1 사이 사인파"""
    return 0.5 + 0.5 * math.sin(TAU * freq * t + phase)


def dofs_idle() -> dict[str, float]:
    return dict(NEUTRAL)


def dofs_walk(t: float) -> dict[str, float]:
    """
    손 전체를 쥐었다 펴는 동작 (실제 사람이 하는 자연스러운 동작).
    4손가락이 같은 위상으로 함께 굴신.
    """
    dofs = dict(NEUTRAL)
    freq = 0.8   # Hz — 너무 빠르면 부자연스럽
    amp  = 50.0
    v = _sin01(t, freq)   # 0~1
    for mcp_k, pip_k, dip_k in [
        ("index_mcp",  "index_pip",  "index_dip"),
        ("middle_mcp", "middle_pip", "middle_dip"),
        ("ring_mcp",   "ring_pip",   "ring_dip"),
        ("pinky_mcp",  "pinky_pip",  "pinky_dip"),
    ]:
        dofs[mcp_k] = 5.0  + amp * v
        dofs[pip_k] = 3.0  + amp * 0.8 * v
        dofs[dip_k] = 2.0  + amp * 0.5 * v
    return dofs


def dofs_wave(t: float) -> dict[str, float]:
    """모든 DOF를 천천히 사인파로 순환 (ROM·축 검증용)"""
    dofs = {}
    for i, (key, (lo, hi)) in enumerate(DOF_RANGES.items()):
        phase = i * (TAU / len(DOF_RANGES))    # DOF마다 위상 다르게
        mid   = (lo + hi) / 2
        amp   = (hi - lo) / 2
        dofs[key] = mid + amp * math.sin(TAU * 0.3 * t + phase)
    return dofs


def dofs_wrist(t: float) -> dict[str, float]:
    """손목 flex/dev 위주 사인파 (horse 머리·목·척추 확인용)"""
    dofs = dict(NEUTRAL)
    dofs["wrist_flex"] = 50.0 * math.sin(TAU * 0.4 * t)
    dofs["wrist_dev"]  = 18.0 * math.sin(TAU * 0.25 * t + TAU / 4)
    dofs["wrist_rot"]  = 40.0 * math.sin(TAU * 0.2 * t + TAU / 3)
    return dofs


def make_hands_angles(mode: str, t: float, steer: float) -> dict[str, dict[str, float]]:
    """
    returns {"left": {...dofs...}, "right": {...dofs...}}
    steer: 오른손 wrist_dev 고정값 (°). 양수=우회전, 음수=좌회전, 0=직진
    """
    if mode == "idle":
        left  = dofs_idle()
        right = dofs_idle()
    elif mode == "walk":
        left  = dofs_walk(t)
        right = dofs_walk(t)   # 오른손도 동일한 걷기 동작
    elif mode == "wave":
        left  = dofs_wave(t)
        right = dofs_wave(t)
    elif mode == "wrist":
        left  = dofs_wrist(t)
        right = dofs_wrist(t)
    else:
        left  = dofs_idle()
        right = dofs_idle()

    # 오른손 wrist_dev로 방향 제어
    if steer != 0.0:
        right["wrist_dev"] = steer

    return {"left": left, "right": right}

# ── 메인 ─────────────────────────────────────────────────────────────────────

def run(animal: str, mapping: str, mode: str, fps: int,
        steer: float, speed: float | None,
        trigger: str | None, trigger_at: float):

    # ── 엔진 초기화 (main.py와 동일) ─────────────────────────────
    print(f"[mock] 엔진 초기화: animal={animal}  mapping={mapping}")

    if mapping in ("blend", "keyframe"):
        from mapping.keyframe_engine import KeyframeMappingEngine
        engine = KeyframeMappingEngine(mappings_dir=MAPPINGS_DIR, poses_dir=POSES_DIR)
    else:
        from mapping.mapping_engine import MappingEngine
        engine = MappingEngine(mappings_dir=MAPPINGS_DIR)

    engine.set_animal(animal)

    # skeleton ROM 로드
    skel_path = os.path.join(POSES_DIR, f"{animal}.json")
    skeleton  = None
    if os.path.exists(skel_path):
        with open(skel_path, encoding="utf-8") as f:
            skeleton = json.load(f)
        print(f"[mock] skeleton ROM 로드: {skel_path}")

    # 로코모션 매퍼 초기화
    from mapping.locomotion_mapper import LocomotionMapper
    loco = LocomotionMapper(animal=animal, mappings_dir=MAPPINGS_DIR)

    # ── 캘리브레이션 (중립 포즈로 즉시 실행) ─────────────────────
    neutral = {"left": dict(NEUTRAL), "right": dict(NEUTRAL)}
    engine.calibrate(neutral)
    loco.calibrate(neutral)
    print("[mock] 캘리브레이션 완료 (중립 포즈 자동 적용)")

    # ── WebSocket 서버 시작 ───────────────────────────────────────
    server = WebSocketServer(port=8765)
    server.start()
    print(f"[mock] mode={mode}  steer={steer:+.1f}°  fps={fps}")
    print("[mock] Unity Play 모드를 시작하면 연결됩니다.")
    print("[mock] Ctrl+C 로 종료\n")

    interval       = 1.0 / fps
    t_start        = time.time()
    triggered      = False
    trigger_dur    = 2.0          # send_trigger와 동일한 duration
    trigger_end_t  = -1.0         # 트리거 종료 시각 (절대 시간 기준)

    try:
        while True:
            t   = time.time() - t_start
            now = time.time()

            # ── 트리거 발사 ──────────────────────────────────────
            if trigger and not triggered and t >= trigger_at:
                server.send_trigger(trigger, duration=trigger_dur)
                trigger_end_t = now + trigger_dur
                print(f"\n[mock] 트리거 발사: {trigger}  ({trigger_dur:.1f}s 동안 joint 전송 중단)")
                triggered = True

            # ── 트리거 재생 중이면 joint 전송 건너뜀 ─────────────
            if triggered and now < trigger_end_t:
                time.sleep(interval)
                continue

            # ── 가짜 hands_angles 생성 ───────────────────────────
            hands_angles = make_hands_angles(mode, t, steer)

            # ── 매핑 엔진 실행 (main.py와 동일한 경로) ───────────
            try:
                joints = engine.transform_clamped(hands_angles, skeleton)
                # direct 모드: float → {x,y,z} 변환 필요
                if joints and not isinstance(next(iter(joints.values())), dict):
                    from main import _float_joints_to_xyz
                    joints = _float_joints_to_xyz(joints, engine.current_animal)
            except Exception as e:
                print(f"\n[mock] 변환 오류: {e}")
                joints = {}

            # ── 로코모션 매퍼 실행 ────────────────────────────────
            loco_result = loco.update(hands_angles, hand_detected=True, engine=engine)
            # --speed 지정 시 speed 값을 강제 덮어씀
            if speed is not None:
                loco_result["speed"] = speed
                loco_result["valid"] = True

            # ── 전송 ────────────────────────────────────────────
            server.send_frame(
                joints        = joints,
                animal        = animal,
                hand_detected = True,
                gesture       = None,
                locomotion    = loco_result,
            )

            # 상태 출력
            speed = loco_result.get("speed", 0)
            yaw   = loco_result.get("yaw_delta", 0)
            print(f"\r[mock] t={t:6.1f}s  joints={len(joints)}  "
                  f"speed={speed:.3f}  yaw={yaw:+.2f}  "
                  f"clients={server.client_count}",
                  end="", flush=True)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n[mock] 종료")
    finally:
        server.stop()

# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="HandAnimal mock: 카메라/MediaPipe 대체 테스트")
    p.add_argument("--animal",     choices=["spider", "horse", "butterfly", "fish"],
                   default="spider")
    p.add_argument("--mapping",    choices=["blend", "keyframe", "direct"],
                   default="blend")
    p.add_argument("--mode",       choices=["idle", "walk", "wave", "wrist"],
                   default="walk",
                   help="idle: 고정, walk: 손가락교대굴신, wave: 전체ROM, wrist: 손목위주")
    p.add_argument("--fps",        type=int,   default=30)
    p.add_argument("--steer",      type=float, default=0.0,
                   help="오른손 wrist_dev 고정값(°). 양수=우, 음수=좌, 0=직진")
    p.add_argument("--speed",      type=float, default=None,
                   help="로코모션 speed 강제 지정 (0.0~1.0). 미지정시 매퍼 자동 계산")
    p.add_argument("--trigger",    type=str,   default=None,
                   help="보낼 트리거 이름 (Attack1, Attack2, Death 등)")
    p.add_argument("--trigger-at", type=float, default=3.0,
                   help="트리거 발사 시점 (시작 후 초, 기본값 3)")
    args = p.parse_args()

    run(
        animal     = args.animal,
        mapping    = args.mapping,
        mode       = args.mode,
        fps        = args.fps,
        steer      = args.steer,
        speed      = args.speed,
        trigger    = args.trigger,
        trigger_at = args.trigger_at,
    )

if __name__ == "__main__":
    main()
