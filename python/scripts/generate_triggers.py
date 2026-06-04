"""
generate_triggers.py
--------------------
각 동물의 트리거 애니메이션을 분석해
locomotion_config.json 의 triggers 블록을 자동 생성.

알고리즘:
  1. 각 트리거 애니메이션의 피크 프레임 찾기 (관절 각도 합산 최대)
  2. 피크 프레임에서 reference_pose_A 대비 가장 크게 움직인 animal joint 찾기
  3. spider_mapping.json 에서 그 joint 에 매핑된 hand DOF 찾기
  4. 역변환: hand_delta = animal_delta / scale_factor
  5. threshold = hand_delta × DELTA_RATIO (DELTA_MIN~DELTA_MAX 클램핑)

실행:
    conda activate capstone_env
    python scripts/generate_triggers.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR     = os.path.join(PYTHON_DIR, "data")
MAPPINGS_DIR = os.path.join(DATA_DIR, "mappings")
POSES_DIR    = os.path.join(DATA_DIR, "animal_skeletons")

LOCO_CONFIG_PATH = os.path.join(MAPPINGS_DIR, "locomotion_config.json")

# 로코모션 예약 DOF — 트리거로 사용 불가
RESERVED_DOFS = {"wrist_dev"}

# 트리거 생성에서 제외할 애니메이션 이름
EXCLUDE_ANIMS = {"Idle", "Rest"}

# 동물별 트리거로 사용할 애니메이션 화이트리스트
# 비어있으면 base_anim/EXCLUDE_ANIMS 제외 전부 사용
TRIGGER_WHITELIST: dict[str, set[str]] = {
    "spider": {"Attack1", "Attack2"},
}

# 역변환된 hand delta 의 몇 % 를 threshold 로 사용
DELTA_RATIO = 0.65
DELTA_MIN   = 15.0   # 최소 threshold (°)
DELTA_MAX   = 50.0   # 최대 threshold (°)
DELTA_ANIM_MIN = 5.0  # 이것보다 작은 animal joint 변화량은 무시


def _load_json(path: str) -> dict | list:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _anim_groups(poses: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for pose in poses:
        anim = pose.get("_anim", "?")
        groups.setdefault(anim, []).append(pose)
    for anim in groups:
        groups[anim].sort(key=lambda p: p.get("_frame", 0))
    return groups


def _peak_frame(frames: list[dict]) -> dict:
    """관절 각도 절댓값 합이 가장 큰 프레임."""
    def _mag(pose: dict) -> float:
        total = 0.0
        for k, v in pose.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                total += sum(abs(x) for x in v.values())
            else:
                total += abs(float(v))
        return total
    return max(frames, key=_mag)


def _scalar(val) -> float:
    """pose 값 → 스칼라 (dict면 절댓값 최대 축)."""
    if isinstance(val, dict):
        return max(val.values(), key=abs)
    return float(val)


def generate_triggers(animal: str, loco_cfg: dict) -> dict:
    """
    한 동물의 triggers 블록 자동 생성.
    반환: {anim_name: {"dof": ..., "hand": ..., "delta": float}}
    """
    poses_path   = os.path.join(POSES_DIR,   f"{animal}_poses.json")
    mapping_path = os.path.join(MAPPINGS_DIR, f"{animal}_mapping.json")

    if not os.path.exists(poses_path):
        print(f"  [{animal}] poses 파일 없음: {poses_path}")
        return {}
    if not os.path.exists(mapping_path):
        print(f"  [{animal}] mapping 파일 없음: {mapping_path}")
        return {}

    poses   = _load_json(poses_path)
    mapping = _load_json(mapping_path)

    ref_A    = mapping.get("reference_pose_A", {})
    map_data = mapping.get("mapping", {})
    base_anim = loco_cfg.get(animal, {}).get("base_anim", "")

    # joint_id → {dof, hand, scale}  (reserved DOF 제외)
    joint_to_dof: dict[str, dict] = {}
    for joint_id, info in map_data.items():
        dof   = info.get("hand_dof_name", "")
        hand  = info.get("hand", "right")
        scale = float(info.get("scale_factor", 0.0))
        if dof and abs(scale) > 1e-6 and dof not in RESERVED_DOFS:
            joint_to_dof[joint_id] = {"dof": dof, "hand": hand, "scale": scale}

    groups   = _anim_groups(poses)
    triggers = {}

    for anim_name, frames in groups.items():
        if anim_name in EXCLUDE_ANIMS or anim_name == base_anim:
            continue
        if anim_name.startswith("?"):
            continue
        whitelist = TRIGGER_WHITELIST.get(animal)
        if whitelist and anim_name not in whitelist:
            continue

        peak = _peak_frame(frames)

        # 각 매핑 가능한 joint 에 대해 (score, joint_id, dof_info, hand_delta) 후보 수집
        candidates = []
        for joint_id, dof_info in joint_to_dof.items():
            raw = peak.get(joint_id)
            if raw is None:
                continue
            anim_val  = _scalar(raw)
            ref_val   = float(ref_A.get(joint_id, 0.0))
            delta_A   = anim_val - ref_val

            if abs(delta_A) < DELTA_ANIM_MIN:
                continue

            hand_delta = delta_A / dof_info["scale"]

            # 양수 delta (더 구부리기) 선호 → score 가중
            score = abs(delta_A) * (1.4 if hand_delta > 0 else 1.0)
            candidates.append((score, joint_id, dof_info, hand_delta))

        if not candidates:
            print(f"  [{animal}] {anim_name}: 유효한 joint 후보 없음, 스킵")
            continue

        candidates.sort(reverse=True)
        _, best_joint, dof_info, hand_delta = candidates[0]

        # threshold 계산
        raw_threshold = abs(hand_delta) * DELTA_RATIO
        threshold     = round(max(DELTA_MIN, min(DELTA_MAX, raw_threshold)), 1)
        if hand_delta < 0:
            threshold = -threshold

        triggers[anim_name] = {
            "dof":   dof_info["dof"],
            "hand":  dof_info["hand"],
            "delta": threshold,
        }

        print(f"  {anim_name:12s}: {dof_info['hand']:5s}.{dof_info['dof']:15s} "
              f"Δanimal={delta_A:+6.1f}°  Δhand={hand_delta:+6.1f}°  "
              f"threshold={threshold:+.1f}°  (joint={best_joint})")

    return triggers


def main():
    if not os.path.exists(LOCO_CONFIG_PATH):
        print(f"[ERROR] 파일 없음: {LOCO_CONFIG_PATH}")
        sys.exit(1)

    loco_cfg: dict = _load_json(LOCO_CONFIG_PATH)
    animals = [k for k in loco_cfg if not k.startswith("_")]

    for animal in animals:
        poses_path = os.path.join(POSES_DIR, f"{animal}_poses.json")
        if not os.path.exists(poses_path):
            continue
        print(f"\n[{animal}]")
        triggers = generate_triggers(animal, loco_cfg)
        if not triggers:
            print(f"  트리거 없음")
            continue
        if animal not in loco_cfg:
            loco_cfg[animal] = {}
        loco_cfg[animal]["triggers"] = triggers

    with open(LOCO_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(loco_cfg, f, ensure_ascii=False, indent=2)
    print(f"\n[완료] {LOCO_CONFIG_PATH} 업데이트")


if __name__ == "__main__":
    main()
