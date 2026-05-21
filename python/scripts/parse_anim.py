"""
parse_anim.py
--------------
Unity .anim 파일에서 아바타 포즈 P를 자동 추출한다.

두 가지 모드:
  --mode bone_map   : .anim 파일을 스캔해 bone_map_{animal}.json 템플릿 자동 생성
                      (skeleton.json joint ID와 퍼지 매칭 후 수동 검토 권장)
  --mode poses      : bone_map_{animal}.json 기반으로 키프레임 샘플링 →
                      {animal}_poses.json 저장

실행 예:
    # 1단계: bone_map 템플릿 생성
    python scripts/parse_anim.py --animal butterfly --mode bone_map \\
        --anim_dir ../unity/Assets/Butterflies/Animations

    # 2단계: 생성된 bone_map 검토/수정 후 포즈 추출
    python scripts/parse_anim.py --animal butterfly --mode poses \\
        --anim_dir ../unity/Assets/Butterflies/Animations

전제:
    - .anim 파일은 Unity text-serialized YAML 형식이어야 한다.
    - skeleton.json 이 data/animal_skeletons/{animal}.json 에 있어야 한다.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKELETONS_DIR = os.path.join(PYTHON_DIR, "data", "animal_skeletons")


# ──────────────────────────────────────────────────────────────
# .anim 파싱
# ──────────────────────────────────────────────────────────────

def _parse_anim_file(anim_path: str) -> dict[str, list[tuple[float, tuple[float,float,float,float]]]]:
    """
    .anim 파일에서 bone path → [(time, (x,y,z,w)), ...] 추출.
    m_RotationCurves 섹션만 파싱 (위치/스케일 제외).
    Unity YAML의 비표준 태그(!u!)는 무시하고 regex로 직접 파싱.
    """
    with open(anim_path, encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # m_RotationCurves 블록만 추출
    rot_block_match = re.search(r"m_RotationCurves:(.*?)(?=\n  m_\w+Curves:|\Z)", text, re.DOTALL)
    if not rot_block_match:
        return {}
    rot_block = rot_block_match.group(1)

    # 각 curve 블록을 path 기준으로 분리
    # path 필드는 curve 블록 마지막에 위치
    curve_blocks = re.split(r"\n  - curve:", rot_block)

    result: dict[str, list] = {}
    for block in curve_blocks[1:]:  # 첫 번째는 빈 문자열
        # path 추출
        path_match = re.search(r"\n    path: (.+)", block)
        if not path_match:
            continue
        path = path_match.group(1).strip()

        # 숫자만인 path(IK hash)는 스킵
        if re.match(r"^\d+$", path):
            continue

        # 키프레임 추출: time + value {x,y,z,w}
        keyframes = []
        kf_pattern = re.compile(
            r"time: ([\d.e+-]+).*?value: \{x: ([\d.e+-]+), y: ([\d.e+-]+), z: ([\d.e+-]+), w: ([\d.e+-]+)\}",
            re.DOTALL,
        )
        for m in kf_pattern.finditer(block):
            t  = float(m.group(1))
            qx = float(m.group(2))
            qy = float(m.group(3))
            qz = float(m.group(4))
            qw = float(m.group(5))
            keyframes.append((t, (qx, qy, qz, qw)))

        if keyframes:
            result[path] = keyframes

    return result


def _quat_to_euler_xyz(q: tuple[float,float,float,float]) -> tuple[float,float,float]:
    """쿼터니언 → Euler ZYX (degrees). Unity 좌표계 기준."""
    x, y, z, w = q
    # Roll (X)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    # Pitch (Y)
    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
    # Yaw (Z)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def _quat_inv(q: tuple) -> tuple:
    x, y, z, w = q
    norm2 = x*x + y*y + z*z + w*w + 1e-12
    return (-x/norm2, -y/norm2, -z/norm2, w/norm2)


def _quat_mul(a: tuple, b: tuple) -> tuple:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    )


def _get_angle_for_axis(euler_xyz: tuple[float,float,float], axis: str) -> float:
    """skeleton.json 의 axis 필드(X/Y/Z)에 해당하는 Euler 값 반환."""
    return {"X": euler_xyz[0], "Y": euler_xyz[1], "Z": euler_xyz[2]}.get(axis.upper(), 0.0)


# ──────────────────────────────────────────────────────────────
# bone_map 자동 생성 (Mode 1)
# ──────────────────────────────────────────────────────────────

def _fuzzy_match(joint_id: str, candidates: list[str]) -> str | None:
    """joint_id 와 가장 유사한 bone path leaf 이름 반환."""
    best_score = 0.0
    best = None
    jid_lower = joint_id.lower().replace("_", "")
    for path in candidates:
        leaf = path.split("/")[-1].lower().replace(".", "").replace("_", "")
        score = SequenceMatcher(None, jid_lower, leaf).ratio()
        if score > best_score:
            best_score = score
            best = path
    return best if best_score > 0.3 else None


def generate_bone_map(animal: str, anim_dir: str, n_poses: int = 10):
    """
    .anim 파일을 스캔해 모든 bone path를 추출하고
    skeleton.json joint ID와 퍼지 매칭하여 bone_map_{animal}.json 템플릿 생성.
    """
    skeleton_path = os.path.join(SKELETONS_DIR, f"{animal}.json")
    if not os.path.exists(skeleton_path):
        print(f"[ERROR] skeleton 파일 없음: {skeleton_path}")
        sys.exit(1)
    with open(skeleton_path, encoding="utf-8") as f:
        skeleton = json.load(f)
    joints = skeleton["joints"]

    # 모든 .anim 에서 bone path 수집
    all_paths: set[str] = set()
    anim_files = [os.path.join(anim_dir, fn) for fn in os.listdir(anim_dir) if fn.endswith(".anim")]
    if not anim_files:
        print(f"[ERROR] .anim 파일 없음: {anim_dir}")
        sys.exit(1)

    for anim_path in anim_files:
        data = _parse_anim_file(anim_path)
        all_paths.update(data.keys())

    moving_paths = sorted(all_paths)
    print(f"[bone_map] 발견된 bone path: {len(moving_paths)}개")

    # 각 joint ID에 대해 퍼지 매칭
    joint_map: dict[str, dict] = {}
    used_paths: set[str] = set()

    for j in joints:
        jid  = j["id"]
        axis = j.get("axis", "X")
        best = _fuzzy_match(jid, [p for p in moving_paths if p not in used_paths])
        joint_map[jid] = {
            "unity_path": best or "TODO",
            "axis":       axis,
            "_match_confidence": "auto" if best else "not_found",
        }
        if best:
            used_paths.add(best)

    # 매핑 안 된 path 목록도 기록
    unmapped = [p for p in moving_paths if p not in used_paths]

    bone_map = {
        "_usage": (
            "joint_map key = skeleton.json joint id. "
            "unity_path = Unity Animator bone path. "
            "axis = 주 회전축 (X/Y/Z). "
            "_match_confidence=auto 는 퍼지 매칭 결과로 검토 필요."
        ),
        "animal":        animal,
        "anim_dir":      anim_dir,
        "rest_anim":     "Idle",
        "skeleton_path": os.path.relpath(skeleton_path, PYTHON_DIR),
        "output_path":   os.path.relpath(skeleton_path, PYTHON_DIR),
        "margin_deg":    5.0,
        "joint_map":     joint_map,
        "_unmapped_paths": unmapped,
    }

    out_path = os.path.join(SKELETONS_DIR, f"bone_map_{animal}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bone_map, f, ensure_ascii=False, indent=2)

    print(f"[OK] bone_map 저장: {out_path}")
    print(f"     매핑됨: {len(joint_map)}개  /  미매핑 path: {len(unmapped)}개")
    print(f"     ★ _match_confidence=auto 항목은 반드시 unity_path 검토 후 수정하세요.")
    if unmapped:
        print(f"     미매핑 paths:")
        for p in unmapped:
            print(f"       {p}")


# ──────────────────────────────────────────────────────────────
# 포즈 추출 (Mode 2)
# ──────────────────────────────────────────────────────────────

def extract_poses(animal: str, anim_dir: str, n_poses: int = 10, rest_anim: str = "Idle"):
    """
    bone_map_{animal}.json 을 읽어 .anim 키프레임에서 포즈 샘플링.
    rest_anim(기본 Idle)의 첫 프레임을 기준 포즈로 사용.
    n_poses 개 대표 포즈를 균등 샘플링해 {animal}_poses.json 저장.
    """
    bone_map_path = os.path.join(SKELETONS_DIR, f"bone_map_{animal}.json")
    if not os.path.exists(bone_map_path):
        print(f"[ERROR] bone_map 없음: {bone_map_path}")
        print(f"        먼저 --mode bone_map 으로 생성하세요.")
        sys.exit(1)

    with open(bone_map_path, encoding="utf-8") as f:
        bone_map = json.load(f)
    joint_map: dict[str, dict] = bone_map["joint_map"]

    # .anim 파일 목록
    anim_files = {
        os.path.splitext(fn)[0]: os.path.join(anim_dir, fn)
        for fn in os.listdir(anim_dir) if fn.endswith(".anim")
    }
    if not anim_files:
        print(f"[ERROR] .anim 파일 없음: {anim_dir}")
        sys.exit(1)

    # rest pose 로드 (Idle 없으면 첫 번째 anim)
    rest_name = rest_anim if rest_anim in anim_files else list(anim_files.keys())[0]
    rest_data = _parse_anim_file(anim_files[rest_name])
    print(f"[poses] rest pose: {rest_name}.anim ({len(rest_data)} bones)")

    # bone path → rest quaternion (t=0 첫 키프레임)
    rest_quats: dict[str, tuple] = {}
    for path, kfs in rest_data.items():
        if kfs:
            rest_quats[path] = sorted(kfs, key=lambda k: k[0])[0][1]

    # 모든 anim 파일에서 키프레임 수집 (애니메이션별로 분리)
    # by_anim: { anim_name: [ {joint: angle, ...}, ... ] }  — 시간순 정렬
    by_anim: dict[str, list[dict]] = {}

    for anim_name, anim_path in sorted(anim_files.items()):
        anim_data = _parse_anim_file(anim_path)

        # 이 anim 파일의 모든 시간 스탬프 수집
        all_times: set[float] = set()
        for path, kfs in anim_data.items():
            for t, _ in kfs:
                all_times.add(t)

        anim_poses: list[dict] = []
        for t in sorted(all_times):
            pose: dict = {}
            for jid, jinfo in joint_map.items():
                unity_path = jinfo.get("unity_path", "TODO")
                if unity_path == "TODO" or unity_path not in anim_data:
                    pose[jid] = {"x": 0.0, "y": 0.0, "z": 0.0}
                    continue

                kfs    = anim_data[unity_path]
                q_curr = min(kfs, key=lambda k: abs(k[0] - t))[1]

                q_rest  = rest_quats.get(unity_path, (0.0, 0.0, 0.0, 1.0))
                q_delta = _quat_mul(_quat_inv(q_rest), q_curr)
                euler   = _quat_to_euler_xyz(q_delta)
                pose[jid] = {"x": round(euler[0], 2), "y": round(euler[1], 2), "z": round(euler[2], 2)}

            if pose:
                # 메타데이터 추가 (_로 시작 → keyframe_engine이 관절값으로 오인하지 않음)
                pose["_anim"]  = anim_name
                pose["_time"]  = round(t, 4)
                anim_poses.append(pose)

        # 중복 제거 (동일 포즈, 메타 제외 비교)
        unique_anim: list[dict] = []
        seen: set[str] = set()
        for p in anim_poses:
            joint_vals = {k: v for k, v in p.items() if not k.startswith("_")}
            key = json.dumps(joint_vals, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_anim.append(p)

        if unique_anim:
            by_anim[anim_name] = unique_anim

    if not by_anim:
        print("[ERROR] 추출된 포즈 없음. bone_map의 unity_path를 확인하세요.")
        sys.exit(1)

    # 애니메이션별 비례 샘플링 (시간 순서 유지)
    # 각 애니메이션에서 최소 1개, 전체 합이 n_poses가 되도록 배분
    total_raw   = sum(len(v) for v in by_anim.values())
    sampled: list[dict] = []

    for anim_name, poses in sorted(by_anim.items()):
        # 이 애니메이션의 비례 할당량
        quota = max(1, round(n_poses * len(poses) / total_raw))
        if len(poses) <= quota:
            picked = poses
        else:
            step  = len(poses) / quota
            picked = [poses[int(i * step)] for i in range(quota)]
        sampled.extend(picked)

    # 각 포즈에 애니메이션 내 프레임 인덱스(_frame) 부여
    frame_counter: dict[str, int] = {}
    for p in sampled:
        anim = p["_anim"]
        frame_counter[anim] = frame_counter.get(anim, 0)
        p["_frame"] = frame_counter[anim]
        frame_counter[anim] += 1

    out_path = os.path.join(SKELETONS_DIR, f"{animal}_poses.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sampled, f, ensure_ascii=False, indent=2)

    total_unique = sum(len(v) for v in by_anim.values())
    print(f"[OK] 포즈 저장: {out_path}")
    print(f"     전체 고유 포즈: {total_unique}  →  저장: {len(sampled)}개")
    print(f"\n  애니메이션별 샘플 수:")
    for anim_name in sorted(by_anim.keys()):
        n_saved = frame_counter.get(anim_name, 0)
        print(f"    {anim_name:20s}: 원본 {len(by_anim[anim_name]):3d}개  →  저장 {n_saved:3d}개")

    # 관절별 각도 범위 출력 (검증용)
    print(f"\n  관절별 각도 범위:")
    for jid in list(joint_map.keys())[:8]:
        get_ax = lambda p, ax: (v.get(ax, 0.0) if isinstance(v := p.get(jid, {}), dict) else 0.0)
        vx = [get_ax(p, "x") for p in sampled]
        vy = [get_ax(p, "y") for p in sampled]
        vz = [get_ax(p, "z") for p in sampled]
        print(f"    {jid:28s}: X[{min(vx):+.1f},{max(vx):+.1f}]  "
              f"Y[{min(vy):+.1f},{max(vy):+.1f}]  "
              f"Z[{min(vz):+.1f},{max(vz):+.1f}] deg")
    if len(joint_map) > 8:
        print(f"    ... 외 {len(joint_map) - 8}개")


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unity .anim → bone_map / poses 자동 추출",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  # 1단계: bone_map 템플릿 생성\n"
            "  python scripts/parse_anim.py --animal butterfly --mode bone_map \\\n"
            "      --anim_dir ../unity/Assets/Butterflies/Animations\n\n"
            "  # 2단계: 포즈 추출\n"
            "  python scripts/parse_anim.py --animal butterfly --mode poses \\\n"
            "      --anim_dir ../unity/Assets/Butterflies/Animations\n"
        ),
    )
    parser.add_argument("--animal",   required=True, help="동물 이름 (spider, butterfly, ...)")
    parser.add_argument("--anim_dir", required=True, help="Unity Animations 폴더 경로")
    parser.add_argument("--mode",     choices=["bone_map", "poses"], default="poses",
                        help="bone_map: 템플릿 생성 / poses: 포즈 추출 (기본)")
    parser.add_argument("--n_poses",  type=int, default=10, help="샘플링할 포즈 수 (기본: 10)")
    parser.add_argument("--rest_anim", default="Idle", help="rest pose 기준 anim 이름 (기본: Idle)")
    args = parser.parse_args()

    anim_dir = os.path.abspath(args.anim_dir)
    if not os.path.isdir(anim_dir):
        print(f"[ERROR] anim_dir 없음: {anim_dir}")
        sys.exit(1)

    if args.mode == "bone_map":
        generate_bone_map(args.animal, anim_dir, args.n_poses)
    else:
        extract_poses(args.animal, anim_dir, args.n_poses, args.rest_anim)


if __name__ == "__main__":
    main()
