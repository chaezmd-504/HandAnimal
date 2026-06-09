"""
test_horse.py — Horse 전용 파이프라인 테스트
=============================================
mock_sender.py와 동일한 구조. 카메라/MediaPipe 없이 Horse에 최적화된
합성 DOF 패턴으로 관절 매핑 + 로코모션 동작 확인.

왼손  = 4다리 (검지→앞왼, 중지→앞오, 약지→뒷왼, 새끼→뒷오)
오른손 = 상체  (wrist_flex→머리/척추, wrist_dev→측면굴곡, 손가락→척추)

모드:
  legs    왼손 4손가락 대각선 대칭 굴신 (실제 말 trot 패턴)
  head    오른손 wrist_flex/dev/rot 사인파 (머리·목·척추 반응 확인)
  walk    전체 보행: legs + 머리 bobbing 합성
  idle    중립 포즈 고정 (캘리브레이션 기준 확인)
  run     빠른 trot (freq 2배, 진폭 ↑)

트리거 (--trigger):
  Horse_001_idle   오른손 검지 굽힘
  Horse_001_run    오른손 약지pip 굽힘
  Horse_001_eat    오른손 새끼mcp 굽힘

실행 예:
  python scripts/test_horse.py
  python scripts/test_horse.py --mode head
  python scripts/test_horse.py --mode walk --speed 1.2
  python scripts/test_horse.py --trigger Horse_001_eat --trigger-at 4
  python scripts/test_horse.py --steer 15
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

MAPPINGS_DIR = str(ROOT / "data" / "mappings")
POSES_DIR    = str(ROOT / "data" / "animal_skeletons")
TAU = 2 * math.pi

# 다리 관절: Unity 걷기 애니메이션이 전담 → Python에서 보내지 않음
# 머리/척추: Python(오른손 wrist)이 제어
_HEAD_SPINE_JOINTS = {
    "scull", "spine_008", "spine_007", "spine_003",
    "spine_002", "spine_001", "spine",
}

# ── 중립 포즈 (캘리브레이션용) ──────────────────────────────────────────────
NEUTRAL: dict[str, float] = {
    "wrist_flex":  0.0,
    "wrist_dev":   0.0,
    "wrist_rot":   0.0,
    "thumb_cmc":   5.0,
    "thumb_abd":  10.0,
    "thumb_mcp":   5.0,
    "thumb_ip":    3.0,
    # 왼손 다리 DOF: ref_H 기준 50°/30° (horse_mapping.json reference_pose_H.left 와 일치)
    "index_mcp":  50.0,  "index_pip":  50.0,  "index_dip":  30.0,
    "middle_mcp": 50.0,  "middle_pip": 50.0,  "middle_dip": 30.0,
    "ring_mcp":   50.0,  "ring_pip":   50.0,  "ring_dip":   30.0,
    "pinky_cmc":   2.0,
    "pinky_mcp":  50.0,  "pinky_pip":  50.0,  "pinky_dip":  30.0,
}

# ── 말 보행 파라미터 ─────────────────────────────────────────────────────────
# Trot: 대각선 발쌍이 동시에 움직임
#   Pair A = 앞왼(index) + 뒷오(pinky)   → phase  0
#   Pair B = 앞오(middle) + 뒷왼(ring)   → phase π (180°)

def _sin01(t: float, freq: float, phase: float = 0.0) -> float:
    return 0.5 + 0.5 * math.sin(TAU * freq * t + phase)


def _leg_flex(t: float, freq: float, amp: float, phase: float) -> dict[str, float]:
    """한 다리(mcp/pip/dip)의 굴신값 반환. amp = 진폭(°). ref 50°/50°/30° 중심으로 진동."""
    v = _sin01(t, freq, phase) - 0.5   # -0.5 ~ +0.5 (중립 기준 대칭)
    return {
        "mcp": 50.0 + amp * v,
        "pip": 50.0 + amp * 0.75 * v,
        "dip": 30.0 + amp * 0.45 * v,
    }


def dofs_legs(t: float, freq: float = 1.0, amp: float = 60.0) -> tuple[dict, dict]:
    """
    왼손: Trot 패턴 (대각선 쌍).
    오른손: 중립 (상체 고정).
    """
    fl = _leg_flex(t, freq, amp, phase=0.0)          # 앞왼 (index)
    fr = _leg_flex(t, freq, amp, phase=TAU / 2)      # 앞오 (middle)  ← 반대 위상
    bl = _leg_flex(t, freq, amp, phase=TAU / 2)      # 뒷왼 (ring)    ← 앞오와 동위상
    br = _leg_flex(t, freq, amp, phase=0.0)           # 뒷오 (pinky)   ← 앞왼과 동위상

    left = dict(NEUTRAL)
    left["index_mcp"]  = fl["mcp"]; left["index_pip"]  = fl["pip"]; left["index_dip"]  = fl["dip"]
    left["middle_mcp"] = fr["mcp"]; left["middle_pip"] = fr["pip"]; left["middle_dip"] = fr["dip"]
    left["ring_mcp"]   = bl["mcp"]; left["ring_pip"]   = bl["pip"]; left["ring_dip"]   = bl["dip"]
    left["pinky_mcp"]  = br["mcp"]; left["pinky_pip"]  = br["pip"]; left["pinky_dip"]  = br["dip"]

    right = dict(NEUTRAL)
    return left, right


def dofs_head(t: float) -> tuple[dict, dict]:
    """
    오른손: 머리·척추 사인파.
      wrist_flex  → skull + spine_008  (고개 끄덕)
      wrist_dev   → spine_007/003      (고개 기울기)
      wrist_rot   → (추가 척추 회전)
    왼손: 중립.
    """
    left  = dict(NEUTRAL)
    right = dict(NEUTRAL)
    right["wrist_flex"] = 40.0 * math.sin(TAU * 0.35 * t)
    right["wrist_dev"]  = 0.0   # 방향 고정 (직진) — LocomotionMapper가 방향 제어에 사용
    right["wrist_rot"]  = 30.0 * math.sin(TAU * 0.18 * t + TAU / 3)
    # 척추 연동 (index/middle/ring mcp 오른손 → spine_002/001/spine)
    v = 0.5 + 0.5 * math.sin(TAU * 0.25 * t)
    right["index_mcp"]  = 5.0 + 20.0 * v
    right["middle_mcp"] = 5.0 + 15.0 * v
    right["ring_mcp"]   = 5.0 + 12.0 * v
    return left, right


def dofs_walk(t: float) -> tuple[dict, dict]:
    """
    Trot (왼손) + 머리 bobbing (오른손 wrist_flex).
    보행 중 말의 머리가 리듬에 맞춰 위아래로 움직이는 패턴.
    """
    freq = 1.0
    left, right = dofs_legs(t, freq=freq, amp=55.0)
    # 머리 bobbing: 보행 주기 2배 속도
    right["wrist_flex"] = 15.0 * math.sin(TAU * freq * 2 * t)
    return left, right


def dofs_run(t: float) -> tuple[dict, dict]:
    """빠른 갤럽 — 빈도·진폭 모두 up."""
    left, right = dofs_legs(t, freq=2.2, amp=70.0)
    right["wrist_flex"] = 20.0 * math.sin(TAU * 2.2 * 2 * t)
    return left, right


def dofs_idle() -> tuple[dict, dict]:
    return dict(NEUTRAL), dict(NEUTRAL)


def dofs_cursor(t: float, freq: float = 1.0, amp: float = 22.0) -> tuple[dict, dict]:
    """
    커서 모드용: ring_pip 진동으로 walk cursor를 구동.
    실제 손 제어에서는 ring_pip 움직임 → cursor 진행량으로 변환됨.
    amp: ring_pip 진폭 (°). 클수록 cursor가 빠르게 진행.
    """
    left = dict(NEUTRAL)
    # ring_pip만 진동 — cursor driver
    left["ring_pip"] = 50.0 + amp * math.sin(TAU * freq * t)
    # 나머지 손가락은 중립 유지
    right = dict(NEUTRAL)
    # 머리 bobbing (보행 주기 2배)
    right["wrist_flex"] = 10.0 * math.sin(TAU * freq * 2 * t)
    return left, right


def make_hands(mode: str, t: float, steer: float) -> dict[str, dict[str, float]]:
    if mode == "legs":
        left, right = dofs_legs(t)
    elif mode == "head":
        left, right = dofs_head(t)
    elif mode == "walk":
        left, right = dofs_walk(t)
    elif mode == "run":
        left, right = dofs_run(t)
    elif mode == "cursor":
        left, right = dofs_cursor(t)
    else:
        left, right = dofs_idle()

    if steer != 0.0:
        right["wrist_dev"] = steer

    return {"left": left, "right": right}


# ── 메인 ─────────────────────────────────────────────────────────────────────

def run(mode: str, mapping: str, fps: int, steer: float, speed: float | None,
        trigger: str | None, trigger_at: float, debug: bool = False,
        cursor_scale: float = 0.12, anim: str = "Horse_001_walk"):

    print(f"[horse-test] 엔진 초기화 ({mapping})")
    if mapping in ("blend", "keyframe"):
        from mapping.keyframe_engine import KeyframeMappingEngine
        engine = KeyframeMappingEngine(mappings_dir=MAPPINGS_DIR, poses_dir=POSES_DIR)
    else:
        from mapping.mapping_engine import MappingEngine
        engine = MappingEngine(mappings_dir=MAPPINGS_DIR)
    engine.set_animal("horse")

    skel_path = os.path.join(POSES_DIR, "horse.json")
    skeleton  = None
    if os.path.exists(skel_path):
        with open(skel_path, encoding="utf-8") as f:
            skeleton = json.load(f)
        print(f"[horse-test] skeleton 로드: {skel_path}")
    else:
        print(f"[horse-test] skeleton 없음 (경로 확인: {skel_path})")

    from mapping.locomotion_mapper import LocomotionMapper
    loco = LocomotionMapper(animal="horse", mappings_dir=MAPPINGS_DIR)

    neutral = {"left": dict(NEUTRAL), "right": dict(NEUTRAL)}
    engine.calibrate(neutral)
    loco.calibrate(neutral)
    print("[horse-test] 캘리브레이션 완료")

    server = WebSocketServer(port=8765)
    server.start()
    print(f"[horse-test] mode={mode}  steer={steer:+.1f}  fps={fps}")
    print("[horse-test] Unity Play 모드 시작 후 연결됩니다.")
    print("[horse-test] Ctrl+C 종료\n")

    interval      = 1.0 / fps
    t_start       = time.time()
    triggered     = False
    trigger_dur   = 2.5
    trigger_end_t = -1.0

    # cursor 모드 상태
    walk_cursor     = 0.0
    prev_ring_pip   = None
    n_frames        = engine.anim_frame_count(anim) if hasattr(engine, "anim_frame_count") else 0

    if mode == "cursor":
        if n_frames == 0:
            print(f"[horse-test] ⚠ anim='{anim}' 프레임 없음. blend/keyframe 엔진인지 확인.")
        else:
            print(f"[horse-test] cursor 모드: anim='{anim}' ({n_frames}frames)  "
                  f"cursor_scale={cursor_scale}")

    try:
        while True:
            t   = time.time() - t_start
            now = time.time()

            # 트리거
            if trigger and not triggered and t >= trigger_at:
                server.send_trigger(trigger, duration=trigger_dur)
                trigger_end_t = now + trigger_dur
                print(f"\n[horse-test] 트리거: {trigger}  ({trigger_dur:.1f}s 중단)")
                triggered = True

            if triggered and now < trigger_end_t:
                time.sleep(interval)
                continue

            # 손 DOF 생성
            hands = make_hands(mode, t, steer)

            # ── cursor 모드: ring_pip 변화량 → walk 커서 → 실제 walk 궤적 ──────
            if mode == "cursor" and n_frames > 0:
                ring_pip_now = hands["left"]["ring_pip"]
                if prev_ring_pip is not None:
                    # abs: 오르막/내리막 모두 cursor를 앞으로 진행
                    # 손가락 진동 1회 = walk cycle 절반 진행
                    delta        = abs(ring_pip_now - prev_ring_pip)
                    walk_cursor  = (walk_cursor + delta * cursor_scale) % n_frames
                prev_ring_pip = ring_pip_now

                joints = engine.get_sequential_pose(anim, walk_cursor)

                # 오른손 wrist_flex → scull/spine 덮어쓰기 (머리 bobbing)
                wf = hands["right"].get("wrist_flex", 0.0)
                for jid in ("scull", "spine_008"):
                    if jid in joints:
                        prev = joints[jid]
                        base = prev.get("z", 0.0) if isinstance(prev, dict) else float(prev)
                        joints[jid] = {"x": 0.0, "y": 0.0, "z": round(base + wf * 0.3, 2)}

            else:
                # ── 기존 매핑 엔진 경로 ───────────────────────────────────────
                try:
                    joints = engine.transform_clamped(hands, skeleton)
                    if joints and not isinstance(next(iter(joints.values())), dict):
                        from main import _float_joints_to_xyz
                        joints = _float_joints_to_xyz(joints, engine.current_animal)
                except Exception as e:
                    print(f"\n[horse-test] 변환 오류: {e}")
                    joints = {}

                # walk/run + direct 모드: 다리는 Unity Walk 애니메이션이 담당 → 머리/척추만 전송
                # blend 모드: 엔진이 전체 관절 직접 제어 → 필터 없음
                if mode in ("walk", "run") and mapping == "direct":
                    joints = {k: v for k, v in joints.items() if k in _HEAD_SPINE_JOINTS}

            # 로코모션
            loco_result = loco.update(hands, hand_detected=True, engine=engine)
            if mode == "cursor":
                # cursor 모드: walk 애니메이션 블렌드 충돌 방지 → speed=0 강제
                # (Unity SetWalkActive(true)가 되면 walk animator + cursor 이중 재생됨)
                loco_result["speed"]     = 0.0
                loco_result["yaw_delta"] = 0.0
                loco_result["valid"]     = True
            elif speed is not None:
                loco_result["speed"]     = speed
                loco_result["yaw_delta"] = 0.0   # 직진 고정
                loco_result["valid"]     = True

            server.send_frame(
                joints        = joints,
                animal        = "horse",
                hand_detected = True,
                gesture       = None,
                locomotion    = loco_result,
            )

            spd = loco_result.get("speed", 0)
            yaw = loco_result.get("yaw_delta", 0)

            # 진단 출력: thigh_l, thigh_r 현재 값 표시
            def _jval(jname):
                v = joints.get(jname)
                if v is None: return "N/A"
                if isinstance(v, dict): return f"{max(v.values(), key=abs):.1f}"
                return f"{v:.1f}"
            thigh_l = _jval("thigh_l")
            thigh_r = _jval("thigh_r")
            ring_pip = hands.get("left", {}).get("ring_pip", 0)

            cursor_str = f"  cur={walk_cursor:.1f}" if mode == "cursor" else ""
            if debug:
                print(f"\n[debug] ring_pip={ring_pip:.1f}  thigh_l={thigh_l}  thigh_r={thigh_r}"
                      f"  joints={len(joints)}{cursor_str}")
            else:
                print(f"\r[horse-test] t={t:6.1f}s  ring_pip={ring_pip:.0f}°"
                      f"  thigh_l={thigh_l}  thigh_r={thigh_r}"
                      f"  joints={len(joints)}  spd={spd:.3f}"
                      f"{cursor_str}  clients={server.client_count}",
                      end="", flush=True)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n[horse-test] 종료")
    finally:
        server.stop()


def main():
    p = argparse.ArgumentParser(description="test_horse.py: Horse 파이프라인 테스트")
    p.add_argument("--mode",    choices=["idle", "legs", "head", "walk", "run", "cursor"],
                   default="head",
                   help="idle:고정 / legs:다리(blend) / head:머리척추 / walk:보행+머리 / run:빠른갤럽 / cursor:walk궤적 그대로")
    p.add_argument("--mapping", choices=["direct", "blend", "keyframe"],
                   default="direct",
                   help="direct: 선형 매핑(horse 권장) / blend|keyframe: 키프레임 블렌딩")
    p.add_argument("--fps",     type=int,   default=30)
    p.add_argument("--steer",   type=float, default=0.0,
                   help="오른손 wrist_dev 고정값(도). 양수=우, 음수=좌")
    p.add_argument("--speed",   type=float, default=None,
                   help="로코모션 speed 강제값 (0.0~3.0). 미지정시 자동")
    p.add_argument("--trigger", type=str,   default=None,
                   help="Horse_001_idle / Horse_001_run / Horse_001_eat")
    p.add_argument("--trigger-at", type=float, default=4.0,
                   help="트리거 발사 시점(초, 기본 4)")
    p.add_argument("--debug", action="store_true",
                   help="매 프레임 관절값 상세 출력")
    p.add_argument("--cursor-scale", type=float, default=0.12,
                   help="cursor 모드: ring_pip 1° 변화 → cursor 진행량 (기본 0.12)")
    p.add_argument("--anim", type=str, default="Horse_001_walk",
                   help="cursor 모드에서 사용할 애니메이션 이름 (기본 Horse_001_walk)")
    args = p.parse_args()

    run(
        mode         = args.mode,
        mapping      = args.mapping,
        fps          = args.fps,
        steer        = args.steer,
        speed        = args.speed,
        trigger      = args.trigger,
        trigger_at   = args.trigger_at,
        debug        = args.debug,
        cursor_scale = args.cursor_scale,
        anim         = args.anim,
    )


if __name__ == "__main__":
    main()
