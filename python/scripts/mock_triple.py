"""
mock_triple.py
--------------
카메라·MediaPipe 없이 자연스러운 손 동작을 수치로 생성하여
Direct / Keyframe / Blend 세 모드를 동시에 Unity로 전송.

포즈 설계 원칙
  - 손 포즈가 실제 공격/피격 동작을 물리적으로 표현
  - Direct 모드도 포즈에 맞게 다리가 움직임
  - Keyframe 모드는 가장 가까운 키프레임으로 스냅

포즈 → 다리 효과
  OPEN     : DIP ≈ ref    → 다리 neutral (기준 위치)
  WALK     : DIP 파도    → 다리 교대 굴신 (걷기)
  ATTACK1  : 검지 강하게 굽힘 → l/r_leg(앞 다리) 오므림 → Attack1 트리거
             (Keyframe은 다리 뻗음 = 반대 방향 → Blend 억제 효과 시각화)

시퀀스 (무한 반복)
  OPEN → WALK → OPEN → ATTACK1 → OPEN → WALK → OPEN

실행:
    conda activate capstone_env
    python scripts/mock_triple.py [--animal spider] [--fps 30]

종료: q 키 또는 Ctrl+C
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mapping.mapping_engine import MappingEngine
from mapping.keyframe_engine import KeyframeMappingEngine
from communication.websocket_server import WebSocketServer

DATA_DIR     = os.path.join(ROOT, "data")
MAPPINGS_DIR = os.path.join(DATA_DIR, "mappings")
POSES_DIR    = os.path.join(DATA_DIR, "animal_skeletons")

PORT   = 8767
ANIMAL = "spider"
TAU    = math.tau

# ── DOF 레퍼런스 (spider_mapping.json ref_H) ─────────────────────────────────
# 이 값이 다리 neutral 기준 — DIP 낮으면 다리 뻗음, 높으면 다리 굽힘
_REF = {"index_dip": 49.89, "middle_dip": 50.6, "ring_dip": 54.78, "pinky_cmc": 17.64}

NEUTRAL: dict[str, float] = {
    "wrist_flex":  0.0,  "wrist_dev":  0.0,  "wrist_rot":  0.0,
    "thumb_cmc":  20.0,  "thumb_abd": 15.0,  "thumb_mcp": 20.0,  "thumb_ip": 15.0,
    "index_mcp":  10.0,  "index_pip": 10.0,  "index_dip":  _REF["index_dip"],
    "middle_mcp": 10.0,  "middle_pip":10.0,  "middle_dip": _REF["middle_dip"],
    "ring_mcp":   10.0,  "ring_pip":  10.0,  "ring_dip":   _REF["ring_dip"],
    "pinky_cmc":  _REF["pinky_cmc"],
    "pinky_mcp":  10.0,  "pinky_pip": 10.0,  "pinky_dip":  10.0,
}

FINGER_GROUPS = [
    ("index",  "index_mcp",  "index_pip",  "index_dip"),
    ("middle", "middle_mcp", "middle_pip", "middle_dip"),
    ("ring",   "ring_mcp",   "ring_pip",   "ring_dip"),
    ("pinky",  "pinky_mcp",  "pinky_pip",  "pinky_dip"),
]

FINGER_PERSONA = {
    "index":  (0.00, 1.00),
    "middle": (0.18, 0.96),
    "ring":   (0.30, 0.93),
    "pinky":  (0.42, 0.88),
}


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def sn(t: float) -> float:
    return math.sin(t)

def smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def lerp_pose(a: dict, b: dict, t: float) -> dict:
    return {k: lerp(a.get(k, 0.0), b.get(k, 0.0), t) for k in set(a) | set(b)}

def noise(t: float, scale: float = 1.0, seed: float = 0.0) -> float:
    return scale * (
        0.5 * sn(t * 7.31 + seed) +
        0.3 * sn(t * 13.7 + seed * 1.7) +
        0.2 * sn(t * 23.1 + seed * 2.3)
    )

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── 손 포즈 ───────────────────────────────────────────────────────────────────

def pose_open(t: float) -> dict:
    """neutral — DIP ≈ ref, 다리 기준 위치"""
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(noise(t, 1.0, 0.0), 0.0, 6.0)
    d["index_dip"]  = _clamp(_REF["index_dip"]  + noise(t, 1.5, 10), 43.0, 57.0)
    d["middle_dip"] = _clamp(_REF["middle_dip"] + noise(t, 1.5, 11), 44.0, 58.0)
    d["ring_dip"]   = _clamp(_REF["ring_dip"]   + noise(t, 1.5, 12), 48.0, 62.0)
    d["pinky_cmc"]  = _clamp(_REF["pinky_cmc"]  + noise(t, 0.8, 13), 13.0, 22.0)
    for name, mcp_k, pip_k, _ in FINGER_GROUPS:
        ph, fr = FINGER_PERSONA[name]
        d[mcp_k] = _clamp(12.0 + noise(t * fr, 1.0, ph * 10),     5.0, 20.0)
        d[pip_k] = _clamp(12.0 + noise(t * fr, 1.2, ph * 10 + 1), 5.0, 20.0)
    return d


def pose_walk(t: float, local_t: float) -> dict:
    """걷기 — 손 펼침(neutral DIP) + 4 DIP 작은 파도 위상 교대
    Direct 다리: DIP 파도 → 교대 굴신
    Keyframe/Blend: Walk 애니메이션 재생 (DIP 값은 Animator에서 대체)"""
    FREQ = 0.5
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(2.0 + noise(t, 0.8, 0.0), -1.0, 5.0)
    # DIP는 ref 근처에서 작게 흔들림 — 손 펼침처럼 보이되 다리는 교대 굴신
    phase_map = {
        "index_dip":  (0.00, 12.0),
        "middle_dip": (0.50, 11.0),
        "ring_dip":   (0.25, 11.0),
        "pinky_cmc":  (0.75,  6.0),
    }
    for i, (dof_k, (ph, amp)) in enumerate(phase_map.items()):
        ref_v = _REF[dof_k]
        cycle = sn(TAU * FREQ * local_t - ph * TAU)
        d[dof_k] = _clamp(ref_v + amp * cycle + noise(t, 0.8, i * 7.0),
                           ref_v - amp - 2, ref_v + amp + 2)
    for name, mcp_k, pip_k, dip_k in FINGER_GROUPS:
        ph_n, fr = FINGER_PERSONA[name]
        d[mcp_k] = _clamp(10.0 + noise(t * fr, 1.0, ph_n * 10),     3.0, 20.0)
        d[pip_k] = _clamp(10.0 + noise(t * fr, 1.2, ph_n * 10 + 1), 3.0, 20.0)
    return d


def pose_attack1(t: float) -> dict:
    """공격1 — 검지 앞으로 뻗음: index_dip 최저 → l/r_leg(앞 다리 쌍) 앞으로 뻗음
    delta = (ref-25) - ref = -25 → l_leg 음수 방향 = 뻗음
    Keyframe Attack1 애니메이션과 같은 방향 → Blend = 0.5×anim + 0.5×hand = 부드러운 공격"""
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(5.0 + noise(t, 1.0, 0.0), 2.0, 8.0)
    # 검지: 앞으로 뻗음 (DIP ref-25 → 앞 다리 뻗음, 음수 delta)
    d["index_dip"]  = _clamp(_REF["index_dip"] - 25 + noise(t, 1.5, 10), 15.0, 30.0)
    d["index_mcp"]  = _clamp(5.0  + noise(t, 2.0, 20), 0.0, 12.0)
    d["index_pip"]  = _clamp(5.0  + noise(t, 2.0, 21), 0.0, 12.0)
    # 나머지 손가락: neutral 유지
    d["middle_dip"] = _clamp(_REF["middle_dip"] + noise(t, 1.5, 11),
                              _REF["middle_dip"] - 5, _REF["middle_dip"] + 5)
    d["ring_dip"]   = _clamp(_REF["ring_dip"]   + noise(t, 1.5, 12),
                              _REF["ring_dip"]   - 5, _REF["ring_dip"]   + 5)
    d["pinky_cmc"]  = _clamp(_REF["pinky_cmc"]  + noise(t, 1.0, 13),
                              _REF["pinky_cmc"]  - 3, _REF["pinky_cmc"]  + 3)
    return d


def pose_recoil(t: float) -> dict:
    """피격 — 모든 손가락 확 오므림 (DIP 전체 높임)
    → 다리 전체 수축·위축 → TakeDamage와 유사한 Direct 출력"""
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(-4.0 + noise(t, 1.0, 0.0), -8.0, 2.0)  # 손목 뒤로 당김
    # DIP 전체 ref + 25° → 모든 다리 최대 굽힘/수축
    d["index_dip"]  = _clamp(_REF["index_dip"]  + 25 + noise(t, 2.0, 10), 65.0, 85.0)
    d["middle_dip"] = _clamp(_REF["middle_dip"] + 25 + noise(t, 2.0, 11), 65.0, 85.0)
    d["ring_dip"]   = _clamp(_REF["ring_dip"]   + 22 + noise(t, 2.0, 12), 68.0, 85.0)
    d["pinky_cmc"]  = _clamp(_REF["pinky_cmc"]  + 15 + noise(t, 1.0, 13), 27.0, 37.0)
    # MCP/PIP도 굽힘
    for name, mcp_k, pip_k, _ in FINGER_GROUPS:
        ph, fr = FINGER_PERSONA[name]
        d[mcp_k] = _clamp(60.0 + noise(t * fr, 2.0, ph * 10),     48.0, 72.0)
        d[pip_k] = _clamp(75.0 + noise(t * fr, 2.5, ph * 10 + 1), 60.0, 85.0)
    return d


def pose_attack_raise(t: float) -> dict:
    """공격+3번 다리 들기 — 검지 뻗음(앞다리) + 약지 강하게 굽힘(l/r_leg_002 들기)
    Direct  : 앞다리 뻗음 + 3번 다리 확실히 들림
    Keyframe: Attack1 재발화 → 표준 공격 (뒷다리 없음)
    Blend   : 0.5×Attack1 + 0.5×손 = 앞다리 약화 + 3번 다리 절반 들림  ← 확연한 차이"""
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(4.0 + noise(t, 1.0, 0.0), 1.0, 7.0)
    # 검지: 뻗음 (앞다리 뻗음 유지, ATTACK1과 동일)
    d["index_dip"]  = _clamp(_REF["index_dip"] - 25 + noise(t, 1.5, 10), 15.0, 30.0)
    d["index_mcp"]  = _clamp(5.0  + noise(t, 2.0, 20), 0.0, 12.0)
    d["index_pip"]  = _clamp(5.0  + noise(t, 2.0, 21), 0.0, 12.0)
    # 약지: 강하게 굽힘 → l/r_leg_002 (3번째 다리 쌍) 들기
    # delta = (+25 - 5deadzone) = +20  →  l_leg_002 = 15.25 + 20×1.2556 ≈ +40°
    d["ring_dip"]   = _clamp(_REF["ring_dip"] + 25 + noise(t, 1.5, 12), 70.0, 85.0)
    d["ring_mcp"]   = _clamp(50.0 + noise(t, 2.0, 30), 38.0, 60.0)
    d["ring_pip"]   = _clamp(65.0 + noise(t, 2.0, 31), 52.0, 76.0)
    # 나머지: neutral 유지
    d["middle_dip"] = _clamp(_REF["middle_dip"] + noise(t, 1.5, 11),
                              _REF["middle_dip"] - 5, _REF["middle_dip"] + 5)
    d["pinky_cmc"]  = _clamp(_REF["pinky_cmc"] + noise(t, 1.0, 13),
                              _REF["pinky_cmc"] - 3, _REF["pinky_cmc"] + 3)
    return d


def pose_attack2(t: float) -> dict:
    """공격2 — 앞 두 손가락(index+middle) DIP 강하게 낮춤, 뒤는 굽힘
    → 앞 두 다리 쌍만 찌름 → Attack2와 유사한 Direct 출력"""
    d = dict(NEUTRAL)
    d["wrist_flex"] = _clamp(10.0 + noise(t, 1.0, 0.0), 5.0, 16.0)
    # index/middle: 최대 뻗음
    d["index_dip"]  = _clamp(_REF["index_dip"]  - 28 + noise(t, 1.5, 10), 12.0, 30.0)
    d["middle_dip"] = _clamp(_REF["middle_dip"] - 25 + noise(t, 1.5, 11), 15.0, 33.0)
    d["index_mcp"]  = _clamp(5.0 + noise(t, 1.0, 20), 0.0, 12.0)
    d["index_pip"]  = _clamp(5.0 + noise(t, 1.2, 21), 0.0, 12.0)
    d["middle_mcp"] = _clamp(5.0 + noise(t, 1.0, 22), 0.0, 12.0)
    d["middle_pip"] = _clamp(5.0 + noise(t, 1.2, 23), 0.0, 12.0)
    # ring/pinky: 뒤다리 굽힘 유지 (균형)
    d["ring_dip"]   = _clamp(_REF["ring_dip"]   + 15 + noise(t, 1.5, 12), 60.0, 78.0)
    d["pinky_cmc"]  = _clamp(_REF["pinky_cmc"]  + 10 + noise(t, 1.0, 13), 22.0, 32.0)
    d["ring_mcp"]   = _clamp(50.0 + noise(t, 1.5, 30), 40.0, 62.0)
    d["ring_pip"]   = _clamp(65.0 + noise(t, 2.0, 31), 52.0, 76.0)
    d["pinky_mcp"]  = _clamp(50.0 + noise(t, 1.5, 32), 40.0, 62.0)
    d["pinky_pip"]  = _clamp(65.0 + noise(t, 2.0, 33), 52.0, 76.0)
    return d


# ── 시퀀스 ────────────────────────────────────────────────────────────────────
# (단계명, 유지(s), 전환(s), walking, trigger_anim|None, trigger_dur(s))

SEQUENCE = [
    ("WALK",         7.0, 0.3, True,  None,      0.0),  # 손 펼침 → 걷기
    ("ATTACK1",      2.0, 0.5, False, "Attack1", 1.5),  # 검지 뻗음 → 앞다리 공격
    ("ATTACK_RAISE", 2.5, 0.5, False, "Attack1", 1.5),  # 앞다리 공격 + 3번 다리 들기
    ("WALK",         7.0, 0.5, True,  None,      0.0),  # 다시 걷기
    ("OPEN",         1.5, 0.5, False, None,      0.0),  # 잠깐 쉬기
]

TOTAL_CYCLE = sum(dur + trans for _, dur, trans, *_ in SEQUENCE)


def get_phase(elapsed: float):
    """→ (단계명, 단계내t, 전환prog, walking, in_transition, step_idx)"""
    t = elapsed % TOTAL_CYCLE
    cursor = 0.0
    for idx, (name, dur, trans, walking, trig_anim, trig_dur) in enumerate(SEQUENCE):
        if trans > 0.0 and t < cursor + trans:
            return name, t - cursor, (t - cursor) / trans, walking, True, idx
        cursor += trans
        if t < cursor + dur:
            return name, t - cursor, 0.0, walking, False, idx
        cursor += dur
    last = SEQUENCE[-1]
    return last[0], last[1], 0.0, last[3], False, len(SEQUENCE) - 1


def _pose_by_name(name: str, t: float, local_t: float) -> dict:
    if name == "WALK":         return pose_walk(t, local_t)
    if name == "ATTACK1":      return pose_attack1(t)
    if name == "ATTACK_RAISE": return pose_attack_raise(t)
    if name == "RECOIL":       return pose_recoil(t)
    if name == "ATTACK2":      return pose_attack2(t)
    return pose_open(t)


def compute_dofs(elapsed: float):
    """→ (dofs, walking, label, step_idx, in_transition)"""
    name, local_t, trans_prog, walking, in_trans, idx = get_phase(elapsed)
    t = elapsed
    current = _pose_by_name(name, t, local_t)
    if in_trans and trans_prog > 0:
        prev_name = SEQUENCE[(idx - 1) % len(SEQUENCE)][0]
        prev_pose = _pose_by_name(prev_name, t, 9999.0)
        current = lerp_pose(prev_pose, current, smoothstep(trans_prog))
    label = ("→ " + name) if in_trans else name
    return current, walking, label, idx, in_trans


# ── bone axes / xyz 변환 ──────────────────────────────────────────────────────

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
            dom_ax = max(("x", sx), ("y", sy), ("z", sz), key=lambda q: q[1])[0]
        sign_sum = sum(
            p.get(jid, {}).get(dom_ax, 0.0)
            for p in poses if isinstance(p.get(jid), dict)
        )
        result[jid] = (dom_ax, -1 if sign_sum < 0 else 1)
    _bone_axes_cache[animal] = result
    return result


def _float_to_xyz(joints: dict, animal: str) -> dict:
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
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    result = {}
    for jid in set(a) | set(b):
        av, bv = a.get(jid, zero), b.get(jid, zero)
        if isinstance(av, dict) and isinstance(bv, dict):
            result[jid] = {ax: av.get(ax, 0.0) * (1 - alpha) + bv.get(ax, 0.0) * alpha
                           for ax in ("x", "y", "z")}
        else:
            result[jid] = av if isinstance(av, dict) else bv
    return result


# ── Hardcoded Blend (손 입력 없이 Keyframe 스케일 + EMA 평활화) ───────────────
# Walk: Keyframe 진폭의 55% → 같은 방향이지만 확연히 작은 움직임
# Attack: 35% + EMA lag → crisp 없이 부드럽게 반응
BLEND_WALK_SCALE   = 0.55   # Walk: 진폭 55% → Keyframe보다 작은 움직임
BLEND_ATTACK_SCALE = 1.0    # Attack: 풀 스트렝스 — EMA lag만으로 Keyframe과 차이
BLEND_IDLE_SCALE   = 0.45
BLEND_SMOOTH_ALPHA = 0.10   # 10% per frame EMA → ~0.3s lag → attack "에즈인" 효과

_blend_smooth: dict[str, dict] = {}


def _hardcode_blend(jk: dict, scale: float) -> dict:
    """jk 값을 scale 배로 줄이고 EMA smoothing 적용 → Blend 포즈"""
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


# ── HUD ──────────────────────────────────────────────────────────────────────

MODE_COLORS = [(0, 200, 255), (0, 255, 100), (255, 160, 0)]
MODE_NAMES  = ["Direct", "Keyframe", "Blend"]

STEP_COLOR = {
    "ATTACK1":      (80,  80,  255),
    "ATTACK_RAISE": (180, 60,  255),
    "ATTACK2":      (255, 80,  80),
    "RECOIL":       (80,  255, 200),
    "WALK":         (0,   220, 120),
    "OPEN":         (160, 160, 160),
}
TRIG_COLOR = {
    "Attack1":    (80,  80,  255),
    "Attack2":    (255, 80,  80),
    "TakeDamage": (80,  255, 200),
}

POSE_HINT = {
    "ATTACK1":      "index extend → front legs reach  |  Keyframe=Attack1  Blend=50%anim+50%hand",
    "ATTACK_RAISE": "index extend + ring curl → front reach + 3rd leg lifts  |  Keyframe=Attack1(crisp)  Blend=partial lift",
    "RECOIL":       "All fingers curl in → all legs contract",
    "WALK":         "4-DOF wave phase → alternating leg stride",
}


def draw_hud(frame: np.ndarray, label: str, step_name: str,
             elapsed: float, walking: bool,
             trigger_active: str | None, trigger_remain: float) -> np.ndarray:
    h, w = frame.shape[:2]
    ov = frame.copy()

    step_col = STEP_COLOR.get(step_name, (200, 200, 200))

    # ── 헤더 ──
    cv2.rectangle(ov, (0, 0), (w, 50), (0, 0, 0), -1)
    walk_tag = "  ▶ WALK ON" if walking else ""
    cv2.putText(ov, f"{label}{walk_tag}", (10, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, step_col, 2)

    # 진행바
    bx, bw_bar = w - 220, 200
    prog = (elapsed % TOTAL_CYCLE) / TOTAL_CYCLE
    cv2.rectangle(ov, (bx, 14), (bx + bw_bar, 32), (40, 40, 40), -1)
    cv2.rectangle(ov, (bx, 14), (bx + int(bw_bar * prog), 32), (0, 160, 255), -1)
    cv2.putText(ov, f"{elapsed:.0f}s", (bx + bw_bar + 4, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (130, 130, 130), 1)

    # ── 트리거 배너 ──
    if trigger_active:
        col = TRIG_COLOR.get(trigger_active, (255, 255, 255))
        cv2.rectangle(ov, (0, 50), (w, 78), (30, 10, 30), -1)
        txt = f"◆ {trigger_active.upper()}  {trigger_remain:.1f}s"
        tw = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.82, 2)[0][0]
        cv2.putText(ov, txt, (w // 2 - tw // 2, 72),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.82, col, 2)
        mode_y = 78
    else:
        mode_y = 50

    # ── 포즈 힌트 ──
    hint = POSE_HINT.get(step_name, "")
    if hint:
        cv2.rectangle(ov, (0, mode_y), (w, mode_y + 20), (20, 20, 8), -1)
        cv2.putText(ov, hint, (8, mode_y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 60), 1)
        mode_y += 20

    # ── 모드 레이블 ──
    cv2.rectangle(ov, (0, mode_y), (w, mode_y + 30), (15, 15, 15), -1)
    for lbl, col, cx in zip(MODE_NAMES, MODE_COLORS,
                             [w // 6, w // 2, w * 5 // 6]):
        tw = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)[0][0]
        cv2.putText(ov, lbl, (cx - tw // 2, mode_y + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, col, 2)

    # ── 다음 트리거 예고 ──
    if not trigger_active:
        t_mod, cursor = elapsed % TOTAL_CYCLE, 0.0
        for nm, dur, trans, wlk, trig_anim, trig_dur in SEQUENCE:
            step_start = cursor + trans
            if trig_anim and t_mod < step_start:
                eta = step_start - t_mod
                col = TRIG_COLOR.get(trig_anim, (180, 180, 180))
                cv2.putText(ov, f"next: {trig_anim}  in {eta:.1f}s",
                            (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
                break
            cursor += trans + dur

    cv2.addWeighted(ov, 0.88, frame, 0.12, 0, frame)
    return frame


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--animal",      default=ANIMAL)
    p.add_argument("--port",        type=int,   default=PORT)
    p.add_argument("--fps",         type=int,   default=30)
    p.add_argument("--blend-alpha", type=float, default=0.5)
    p.add_argument("--temperature", type=float, default=8.0)
    p.add_argument("--no-display",  action="store_true")
    args = p.parse_args()

    print(f"[mock_triple] 엔진 초기화: animal={args.animal}")
    engine_direct   = MappingEngine(MAPPINGS_DIR)
    engine_keyframe = KeyframeMappingEngine(MAPPINGS_DIR, POSES_DIR,
                                            temperature=args.temperature)
    engine_direct.set_animal(args.animal)
    engine_keyframe.set_animal(args.animal)

    server = WebSocketServer(port=args.port)
    server.start()
    print(f"[mock_triple] ws://localhost:{args.port}  cycle={TOTAL_CYCLE:.1f}s  FPS={args.fps}")
    print(f"[mock_triple] 시퀀스: WALK→ATTACK1(검지굽힘)→WALK→OPEN (반복)  temp={args.temperature}")
    print("[mock_triple] 종료: q 또는 Ctrl+C\n")

    interval    = 1.0 / args.fps
    t_start     = time.time()
    last_step   = -1
    trig_active = None
    trig_end    = 0.0

    try:
        while True:
            t0      = time.time()
            elapsed = t0 - t_start

            dofs, walking, label, step_idx, in_trans = compute_dofs(elapsed)
            step_name = SEQUENCE[step_idx][0]
            hands = {"left": dofs, "right": dofs}

            # ── Direct ──
            try:
                jd_raw = engine_direct.transform_bilateral(hands)
                if jd_raw and not isinstance(next(iter(jd_raw.values())), dict):
                    jd = _float_to_xyz(jd_raw, args.animal)
                else:
                    jd = jd_raw or {}
            except Exception as e:
                print(f"\n[Direct 오류] {e}"); jd = {}

            # ── 좌우 대칭 보정: r_leg = mirror of l_leg (x축 반전) ──
            for _i in range(4):
                _suf = "" if _i == 0 else f"_{_i:03d}"
                _lk, _rk = f"l_leg{_suf}", f"r_leg{_suf}"
                if _lk in jd and _rk in jd:
                    lv = jd[_lk]
                    jd[_rk] = {"x": -lv.get("x", 0.0),
                                "y":  lv.get("y", 0.0),
                                "z": -lv.get("z", 0.0)}

            # ── Keyframe ──
            try:
                jk_raw = engine_keyframe.transform_bilateral(hands) or {}
                safe = set(jd.keys())
                jk = {k: v for k, v in jk_raw.items() if k in safe} if safe else jk_raw
            except Exception as e:
                print(f"\n[Keyframe 오류] {e}"); jk = {}

            # ── Blend 계산: 항상 raw jd (boost 전) ──
            # Unity LateUpdate: Slerp(anim_pose, handPose, 0.5)
            # Walk  → Slerp(Walk_anim,   jd, 0.5)
            # Attack→ Slerp(Attack1_anim, jd, 0.5)  (blendCtrl도 PlayTriggerAnim)
            jb = {k: dict(v) for k, v in jd.items()}

            # ── Direct boost (더 강한 움직임) ──
            if jd:
                _boost = 2.5 if step_name in ("ATTACK1", "ATTACK2") else 1.8
                jd = {k: {ax: v.get(ax, 0.0) * _boost for ax in ("x", "y", "z")}
                      for k, v in jd.items()}

            # ── 트리거 판정 (triple_frame에 포함) ──
            fire_anim = None
            fire_dur  = 0.0
            if step_idx != last_step:
                last_step = step_idx
                _, _, _, _, trig_anim, trig_dur = SEQUENCE[step_idx]
                if trig_anim:
                    fire_anim   = trig_anim
                    fire_dur    = trig_dur
                    trig_active = trig_anim
                    trig_end    = t0 + trig_dur
                    print(f"\n[trigger] {trig_anim}  ({trig_dur}s)")

            # ── triple_frame 전송 (트리거 포함) ──
            frame_msg = {
                "type":          "triple_frame",
                "hand_detected": True,
                "speed":         1.0 if walking else 0.0,
                "direct":        jd,
                "keyframe":      jk,
                "blend":         jb,
            }
            if fire_anim:
                frame_msg["trigger_anim"]     = fire_anim
                frame_msg["trigger_duration"] = fire_dur
            server._broadcast(json.dumps(frame_msg))

            if trig_active and t0 >= trig_end:
                trig_active = None

            trig_remain = max(0.0, trig_end - t0)

            # ── 시각화 ──
            if not args.no_display:
                canvas = np.zeros((480, 640, 3), dtype=np.uint8)
                canvas = draw_hud(canvas, label, step_name,
                                  elapsed, walking, trig_active, trig_remain)
                cv2.imshow("mock_triple (q=종료)", canvas)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            print(f"\r[t={elapsed:6.1f}s] {label:<18} walk={'ON ' if walking else 'OFF'}"
                  f"  trig={trig_active or '-':<12}"
                  f"  D={len(jd):2d} K={len(jk):2d}  clients={server.client_count}",
                  end="", flush=True)

            dt = time.time() - t0
            if dt < interval:
                time.sleep(interval - dt)

    except KeyboardInterrupt:
        print("\n[mock_triple] 종료")
    finally:
        if not args.no_display:
            cv2.destroyAllWindows()
        server.stop()


if __name__ == "__main__":
    main()
