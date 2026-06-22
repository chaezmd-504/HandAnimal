"""
demo_triple.py
--------------
Direct / Keyframe / Blend 세 가지 모드를 동시에 Unity로 전송.
Unity TripleModeClient 가 세 마리 거미에 각각 적용해 한 화면에 표시.

실행:
    conda activate capstone_env
    python scripts/demo_triple.py [--animal spider]
    python scripts/demo_triple.py --video path/to/file.mp4   # 영상 파일로 실행

종료: q 키
"""

from __future__ import annotations

import json
import os
import sys
import time

import cv2
import numpy as np

_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import mediapipe as mp

from tracking.hand_tracker import download_model, MODEL_PATH, compute_dof_angles, draw_landmarks
from tracking.occlusion_handler import OcclusionHandler
from mapping.mapping_engine import MappingEngine
from mapping.keyframe_engine import KeyframeMappingEngine
from communication.websocket_server import WebSocketServer

BaseOptions           = mp.tasks.BaseOptions
HandLandmarker        = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode     = mp.tasks.vision.RunningMode

DATA_DIR     = os.path.join(_PYTHON_DIR, "data")
MAPPINGS_DIR = os.path.join(DATA_DIR, "mappings")
POSES_DIR    = os.path.join(DATA_DIR, "animal_skeletons")

PORT   = 8767
ANIMAL = "spider"

# ── 관절 축 캐시 (main.py 동일 로직) ────────────────────────────
_bone_axes_cache: dict[str, dict] = {}

def _get_bone_axes(animal: str) -> dict:
    if animal in _bone_axes_cache:
        return _bone_axes_cache[animal]
    poses_path = os.path.join(POSES_DIR, f"{animal}_poses.json")
    if not os.path.exists(poses_path):
        _bone_axes_cache[animal] = {}
        return {}
    with open(poses_path, encoding="utf-8") as f:
        poses = json.load(f)
    bone_map_path = os.path.join(POSES_DIR, f"bone_map_{animal}.json")
    axis_map: dict[str, str] = {}
    if os.path.exists(bone_map_path):
        with open(bone_map_path, encoding="utf-8") as f:
            bm = json.load(f)
        for jid, info in bm.get("joint_map", {}).items():
            axis_map[jid] = info.get("axis", "Y").lower()
    joints: set[str] = set(k for p in poses for k in p if not k.startswith("_"))
    result: dict = {}
    for jid in joints:
        if jid in axis_map:
            dom_ax = axis_map[jid]
        else:
            sx = sy = sz = 0.0
            for p in poses:
                v = p.get(jid)
                if not isinstance(v, dict):
                    continue
                sx += abs(v.get("x", 0.0))
                sy += abs(v.get("y", 0.0))
                sz += abs(v.get("z", 0.0))
            dom_ax = max(("x", sx), ("y", sy), ("z", sz), key=lambda t: t[1])[0]
        sign_sum = sum(
            p.get(jid, {}).get(dom_ax, 0.0)
            for p in poses if isinstance(p.get(jid), dict)
        )
        result[jid] = (dom_ax, -1 if sign_sum < 0 else 1)
    _bone_axes_cache[animal] = result
    return result


def _float_to_xyz(joints: dict, animal: str) -> dict[str, dict]:
    axes = _get_bone_axes(animal)
    out = {}
    for jid, val in joints.items():
        ax, sign = axes.get(jid, ("y", 1))
        sv = float(val) * sign
        out[jid] = {
            "x": sv if ax == "x" else 0.0,
            "y": sv if ax == "y" else 0.0,
            "z": sv if ax == "z" else 0.0,
        }
    return out


def _blend_joints(a: dict, b: dict, alpha: float = 0.5) -> dict:
    """a*(1-alpha) + b*alpha. 모든 관절 키 합집합 처리."""
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    result = {}
    for jid in set(a) | set(b):
        av = a.get(jid, zero)
        bv = b.get(jid, zero)
        if isinstance(av, dict) and isinstance(bv, dict):
            result[jid] = {
                ax: av.get(ax, 0.0) * (1 - alpha) + bv.get(ax, 0.0) * alpha
                for ax in ("x", "y", "z")
            }
        elif isinstance(av, dict):
            result[jid] = av
        else:
            result[jid] = bv
    return result


# ── Hardcoded Blend (손 입력 없음, Keyframe 스케일 + EMA 평활화) ──────────────
BLEND_WALK_SCALE   = 0.55   # Walk: 다리 진폭 55% → Keyframe보다 작은 움직임
BLEND_ATTACK_SCALE = 1.0    # Attack: 풀 스트렝스 — EMA lag만으로 Keyframe과 차이
BLEND_SMOOTH_ALPHA = 0.10   # EMA alpha → ~0.3s lag → attack "에즈인" 효과

_blend_smooth: dict[str, dict] = {}


def _hardcode_blend(jk: dict, scale: float) -> dict:
    """jk 값을 scale 배로 줄이고 EMA smoothing 적용 → 손 입력 없는 Blend 포즈"""
    global _blend_smooth
    scaled = {k: {ax: v.get(ax, 0.0) * scale for ax in ("x", "y", "z")}
              for k, v in jk.items()}
    result = {}
    for k, tv in scaled.items():
        prev = _blend_smooth.get(k, tv)
        smoothed = {ax: prev.get(ax, 0.0) * (1 - BLEND_SMOOTH_ALPHA)
                        + tv.get(ax, 0.0) * BLEND_SMOOTH_ALPHA
                    for ax in ("x", "y", "z")}
        _blend_smooth[k] = smoothed
        result[k] = smoothed
    return result


# ── 트리거 제스처 ────────────────────────────────────────────────────
# 검지(index_dip)만 크게 굽히면 Attack1 트리거
_TRIGGER_ANIMS = {
    "index_bent": ("Attack1", 1.5),
}
_TRIGGER_COOLDOWN = 3.5   # 초 (트리거 간 최소 간격)
_GESTURE_THRESHOLD = 15.0  # 캘리브레이션 기준값 대비 굽힘 임계치 (°)

# 캘리브레이션 후 갱신됨
_calib_ref_idip: dict[str, float] = {"left": 49.89, "right": 49.89}

def _classify_gesture(hands_angles: dict) -> str | None:
    """캘리브레이션 기준 대비 검지 굽힘으로 Attack1 트리거."""
    for side in ("right", "left"):
        dofs = hands_angles.get(side)
        if not dofs:
            continue
        i  = dofs.get("index_dip", _calib_ref_idip[side])
        ri = _calib_ref_idip[side]
        if i - ri > _GESTURE_THRESHOLD:
            return "index_bent"
    return None


def _is_walking(hands_angles: dict, last_trigger_time: float) -> bool:
    """손이 감지되어 있고 트리거 직후 1.5s가 아닌 경우 walk."""
    if not hands_angles:
        return False
    return time.time() - last_trigger_time >= 1.5


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--animal", default=ANIMAL)
    p.add_argument("--port",   type=int, default=PORT)
    p.add_argument("--temperature", type=float, default=8.0)
    p.add_argument("--video",  default=None,
                   help="영상 파일 경로 (미지정 시 웹캠 0 사용)")
    args = p.parse_args()

    download_model()

    # ── 트리거 상태 ────────────────────────────────────────────
    last_trigger_time    = 0.0        # 마지막 트리거 전송 시각
    last_gesture         = None       # 직전 프레임 제스처 (연속 중복 방지)
    gesture_hold_count   = 0          # 동일 제스처 연속 프레임 수
    _GESTURE_CONFIRM     = 3          # 이 프레임 수 이상 유지 시 트리거 발화

    # ── 엔진 초기화 ────────────────────────────────────────────
    engine_direct  = MappingEngine(MAPPINGS_DIR)
    engine_keyframe = KeyframeMappingEngine(MAPPINGS_DIR, POSES_DIR, temperature=args.temperature)

    engine_direct.set_animal(args.animal)
    engine_keyframe.set_animal(args.animal)

    _CALIB_DURATION  = 5.0
    calib_done       = False
    calib_start      = time.time()
    calib_reject_msg = ""
    calib_retry_until = 0.0

    # ── 손 추적 ────────────────────────────────────────────────
    occlusion       = {"left": OcclusionHandler(), "right": OcclusionHandler()}
    occlusion_world = {"left": OcclusionHandler(), "right": OcclusionHandler()}

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    # ── WebSocket 서버 ─────────────────────────────────────────
    server = WebSocketServer(port=args.port)
    server.start()
    print(f"[demo_triple] ws://localhost:{args.port}  animal={args.animal}")
    print("[demo_triple] Unity: TripleModeSceneSetup 메뉴 실행 후 Play")
    print("[demo_triple] 종료: q")

    video_src = args.video if args.video else 0
    cap = cv2.VideoCapture(video_src)
    is_file = args.video is not None
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_idx  = 0   # 영상 파일 타임스탬프 계산용 (단조 증가 보장)

    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                if is_file:
                    # 영상 끝 → 처음부터 반복
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                    if not ret:
                        break
                else:
                    break
            frame = cv2.flip(frame, 1)
            # 영상 파일: 단조 증가 타임스탬프 (루프해도 계속 증가)
            if is_file:
                ts_ms = int(frame_idx * 1000.0 / video_fps)
            else:
                ts_ms = int(time.time() * 1000)
            frame_idx += 1

            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_img, ts_ms)

            hand_detected = bool(result.hand_landmarks)
            hands_angles: dict = {}

            if hand_detected:
                for lm, wlm, handedness in zip(
                    result.hand_landmarks,
                    result.hand_world_landmarks,
                    result.handedness,
                ):
                    side = handedness[0].category_name.lower()
                    img_f  = occlusion[side].process(lm)
                    wld_f  = occlusion_world[side].process(wlm)
                    hands_angles[side] = compute_dof_angles(wld_f, img_f)
                    draw_landmarks(frame, lm, frame.shape[0], frame.shape[1],
                                   color=(0, 255, 0) if side == "left" else (255, 100, 0))

            # 한 손만 잡혀도 양쪽 다리 모두 움직이도록 미러링
            if hand_detected and hands_angles:
                if "right" in hands_angles and "left" not in hands_angles:
                    hands_angles["left"] = hands_angles["right"]
                elif "left" in hands_angles and "right" not in hands_angles:
                    hands_angles["right"] = hands_angles["left"]

            # ── 캘리브레이션 카운트다운 ────────────────────────
            if not calib_done:
                now       = time.time()
                remaining = _CALIB_DURATION - (now - calib_start)
                if remaining <= 0:
                    if hands_angles:
                        # threshold=180 → 어떤 포즈든 거부 없이 캘리브레이션
                        engine_direct.calibrate(hands_angles, threshold=180.0)
                        engine_keyframe.calibrate(hands_angles)
                        calib_done = True
                        calib_reject_msg = ""
                        # 캘리브레이션 기준 index_dip 저장 (제스처 감지에 사용)
                        for side in ("left", "right"):
                            if side in hands_angles:
                                ref_val = hands_angles[side].get("index_dip", _calib_ref_idip[side])
                                _calib_ref_idip[side] = ref_val
                        print(f"[calib] 완료 — {list(hands_angles.keys())}  "
                              f"ref_idip={_calib_ref_idip}")
                    else:
                        calib_start = time.time()  # 손 없으면 카운트다운 재시작

                # 오버레이
                h_f, w_f = frame.shape[:2]
                ov = frame.copy()
                cv2.rectangle(ov, (0, 0), (w_f, h_f), (0, 0, 0), -1)
                cv2.addWeighted(ov, 0.5, frame, 0.5, 0, frame)
                cv2.putText(frame, "Calibration", (20, 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
                cv2.putText(frame, "손가락 살짝 굽히고 (10-20 deg) 유지",
                            (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 1)
                cnt = str(max(1, int(remaining) + 1))
                tsz = cv2.getTextSize(cnt, cv2.FONT_HERSHEY_SIMPLEX, 5.0, 8)[0]
                col = (0, 255, 255) if hand_detected else (80, 80, 255)
                cv2.putText(frame, cnt,
                            (w_f // 2 - tsz[0] // 2, h_f // 2 + 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 5.0, col, 8)
                msg_line = (calib_reject_msg if calib_reject_msg
                            else ("Hands detected — hold pose" if hand_detected
                                  else "카메라에 손을 보여주세요"))
                cv2.putText(frame, msg_line, (20, h_f - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 80, 255) if calib_reject_msg else
                            ((0, 220, 100) if hand_detected else (80, 80, 255)), 2)

            # ── 세 가지 모드 계산 (calibration 완료 후에만) ────
            if calib_done and hand_detected and hands_angles:
                # Direct
                joints_d_raw = engine_direct.transform_bilateral(hands_angles)
                if joints_d_raw and not isinstance(next(iter(joints_d_raw.values())), dict):
                    joints_direct = _float_to_xyz(joints_d_raw, args.animal)
                else:
                    joints_direct = joints_d_raw

                # 좌우 대칭 보정: r_leg = mirror of l_leg (z/x 반전)
                for _i in range(4):
                    _suf = "" if _i == 0 else f"_{_i:03d}"
                    _lk, _rk = f"l_leg{_suf}", f"r_leg{_suf}"
                    if _lk in joints_direct and _rk in joints_direct:
                        lv = joints_direct[_lk]
                        joints_direct[_rk] = {"x": -lv.get("x", 0.0),
                                              "y":  lv.get("y", 0.0),
                                              "z": -lv.get("z", 0.0)}

                # Keyframe — body 관절(bone 등) 필터링으로 Death 뒤집힘 방지
                jk_raw = engine_keyframe.transform_bilateral(hands_angles)
                if joints_direct:
                    _safe = set(joints_direct.keys())
                    joints_keyframe = {k: v for k, v in jk_raw.items() if k in _safe}
                else:
                    joints_keyframe = jk_raw

                # Blend: 항상 raw joints_direct (boost 전)
                # Walk  → Unity: Slerp(Walk_anim,    jd, 0.5)
                # Attack→ Unity: Slerp(Attack1_anim,  jd, 0.5)  (blendCtrl도 PlayTriggerAnim)
                joints_blend = {k: dict(v) for k, v in joints_direct.items()}

                # Direct boost (더 강한 움직임)
                joints_direct = {k: {ax: v.get(ax, 0.0) * 1.8
                                     for ax in ("x", "y", "z")}
                                 for k, v in joints_direct.items()}

                # 트리거 감지
                fire_anim = None
                fire_dur  = 0.0
                gesture = _classify_gesture(hands_angles)
                now = time.time()
                if gesture == last_gesture and gesture is not None:
                    gesture_hold_count += 1
                else:
                    gesture_hold_count = 1
                last_gesture = gesture

                if (gesture is not None
                        and gesture_hold_count == _GESTURE_CONFIRM
                        and now - last_trigger_time >= _TRIGGER_COOLDOWN):
                    fire_anim, fire_dur = _TRIGGER_ANIMS[gesture]
                    last_trigger_time = now
                    print(f"[trigger] {gesture} → {fire_anim} ({fire_dur}s)")

                # Walk: 손 감지 중이고 트리거 직후 1.5s가 아니면 걷기
                walking = _is_walking(hands_angles, last_trigger_time)

                frame_msg = {
                    "type":          "triple_frame",
                    "hand_detected": True,
                    "speed":         1.0 if walking else 0.0,
                    "direct":        joints_direct,
                    "keyframe":      joints_keyframe,
                    "blend":         joints_blend,
                }
                if fire_anim:
                    frame_msg["trigger_anim"]     = fire_anim
                    frame_msg["trigger_duration"] = fire_dur
                msg = json.dumps(frame_msg)
            else:
                msg = json.dumps({
                    "type":         "triple_frame",
                    "hand_detected": False,
                    "direct":       {},
                    "keyframe":     {},
                    "blend":        {},
                })

            server._broadcast(msg)

            # ── HUD ───────────────────────────────────────────
            h, w = frame.shape[:2]
            labels = [
                ("Direct",   (0, 200, 255),  w // 6),
                ("Keyframe", (0, 255, 100),  w // 2),
                ("Blend",    (255, 160, 0),  w * 5 // 6),
            ]
            cv2.rectangle(frame, (0, 0), (w, 40), (0, 0, 0), -1)
            for label, color, cx in labels:
                tw = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0][0]
                cv2.putText(frame, label, (cx - tw // 2, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            if calib_done:
                status = "DETECTED" if hand_detected else "NO HAND"
                cv2.putText(frame, status, (10, h - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 255, 0) if hand_detected else (0, 80, 255), 2)
                # 제스처 / 트리거 표시
                now_t = time.time()
                trig_remain = _TRIGGER_COOLDOWN - (now_t - last_trigger_time)
                if last_trigger_time > 0 and trig_remain > 0:
                    cd_txt = f"cooldown {trig_remain:.1f}s"
                    cv2.putText(frame, cd_txt, (10, h - 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 180, 255), 1)
                elif gesture is not None:
                    gest_txt = f"gesture: {gesture}  ({gesture_hold_count}/{_GESTURE_CONFIRM}) {'WALK' if walking else ''}"
                    cv2.putText(frame, gest_txt, (10, h - 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 50), 1)
                elif walking:
                    cv2.putText(frame, "WALK", (10, h - 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 120), 1)

            cv2.imshow("demo_triple", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()
    server.stop()


if __name__ == "__main__":
    main()
