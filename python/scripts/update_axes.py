"""
update_axes.py
--------------
{animal}_poses.json의 실제 애니메이션 데이터에서 각 관절의 지배 축(dominant axis)을
자동 계산해 skeleton.json 과 bone_map_{animal}.json 의 axis 필드를 갱신한다.

왜 필요한가:
    parse_anim.py --mode bone_map 은 axis 를 skeleton.json 에서 복사하기만 한다.
    skeleton.json 의 axis 는 AnimPoseExporter 가 생성할 때 좌표계 변환 오류로
    실제 Unity 애니메이션 축과 다를 수 있다.
    poses.json 은 실제 .anim 쿼터니언에서 추출했으므로 이것이 정본이다.

실행:
    python scripts/update_axes.py --animal spider
    python scripts/update_axes.py --animal spider --dry-run   # 변경 내용만 출력

출력:
    - skeleton.json axis 필드 갱신
    - bone_map_{animal}.json axis 필드 갱신
    - 변경 내용 및 데이터 없는 관절 목록 출력
"""

import argparse
import json
import os
import sys

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR  = os.path.dirname(SCRIPT_DIR)
SKEL_DIR    = os.path.join(PYTHON_DIR, "data", "animal_skeletons")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def dominant_axis(poses: list[dict], joint_id: str) -> str | None:
    """
    poses.json 에서 joint_id 의 지배 축을 계산한다.
    각 축의 abs 합산이 가장 큰 축 반환 (X/Y/Z 대문자).
    모든 값이 0 이거나 데이터 없으면 None 반환.
    """
    sx = sy = sz = 0.0
    count = 0
    for p in poses:
        v = p.get(joint_id)
        if not isinstance(v, dict):
            continue
        sx += abs(v.get("x", 0.0))
        sy += abs(v.get("y", 0.0))
        sz += abs(v.get("z", 0.0))
        count += 1

    if count == 0 or (sx == 0.0 and sy == 0.0 and sz == 0.0):
        return None

    return max(("X", sx), ("Y", sy), ("Z", sz), key=lambda t: t[1])[0]


def update_axes(animal: str, dry_run: bool = False):
    poses_path    = os.path.join(SKEL_DIR, f"{animal}_poses.json")
    skeleton_path = os.path.join(SKEL_DIR, f"{animal}.json")
    bone_map_path = os.path.join(SKEL_DIR, f"bone_map_{animal}.json")

    # ── 파일 로드 ────────────────────────────────────────────────
    if not os.path.exists(poses_path):
        print(f"[ERROR] poses 없음: {poses_path}")
        print(f"        먼저 parse_anim.py --mode poses 를 실행하세요.")
        sys.exit(1)
    if not os.path.exists(skeleton_path):
        print(f"[ERROR] skeleton 없음: {skeleton_path}")
        sys.exit(1)

    with open(poses_path,    encoding="utf-8") as f:
        poses = json.load(f)
    with open(skeleton_path, encoding="utf-8") as f:
        skeleton = json.load(f)

    bone_map = None
    if os.path.exists(bone_map_path):
        with open(bone_map_path, encoding="utf-8") as f:
            bone_map = json.load(f)

    # ── 관절별 지배 축 계산 ──────────────────────────────────────
    results: dict[str, str | None] = {}
    for joint in skeleton["joints"]:
        jid = joint["id"]
        results[jid] = dominant_axis(poses, jid)

    # ── 변경 내용 출력 ───────────────────────────────────────────
    changed_skel     = []
    no_data          = []
    oscillating      = []   # 데이터는 있지만 sign_sum ≈ 0 (walk cycle 등)

    for joint in skeleton["joints"]:
        jid      = joint["id"]
        old_axis = joint["axis"]
        new_axis = results[jid]

        if new_axis is None:
            no_data.append(jid)
            continue

        # sign_sum 도 같이 출력 (참고용)
        sign_sum = sum(
            p.get(jid, {}).get(new_axis.lower(), 0.0)
            for p in poses if isinstance(p.get(jid), dict)
        )
        if abs(sign_sum) < 5.0:
            oscillating.append(f"{jid} (sign_sum={sign_sum:.1f})")

        if old_axis.upper() != new_axis:
            changed_skel.append((jid, old_axis, new_axis))

    print(f"\n[update_axes] {animal.upper()} — {'DRY RUN' if dry_run else '적용'}")
    print(f"{'─'*55}")

    if changed_skel:
        print(f"\n  skeleton.json axis 변경 ({len(changed_skel)}개):")
        for jid, old, new in changed_skel:
            print(f"    {jid:18s}  {old} → {new}")
    else:
        print(f"\n  skeleton.json: 변경 없음 (모든 axis 일치)")

    if no_data:
        print(f"\n  [WARNING] 포즈 데이터 없음 → axis 유지 ({len(no_data)}개):")
        for jid in no_data:
            print(f"    {jid}")

    if oscillating:
        print(f"\n  [INFO] 진동 관절 (sign_sum ≈ 0, 방향 신뢰도 낮음):")
        for msg in oscillating:
            print(f"    {msg}")

    if dry_run:
        print(f"\n  --dry-run: 파일 미수정")
        return

    # ── skeleton.json 갱신 ───────────────────────────────────────
    for joint in skeleton["joints"]:
        jid      = joint["id"]
        new_axis = results[jid]
        if new_axis is not None:
            joint["axis"] = new_axis

    with open(skeleton_path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)
    print(f"\n  [OK] skeleton.json 갱신: {skeleton_path}")

    # ── bone_map 갱신 ────────────────────────────────────────────
    if bone_map is not None:
        changed_bm = 0
        for jid, new_axis in results.items():
            if new_axis is None:
                continue
            if jid in bone_map.get("joint_map", {}):
                old = bone_map["joint_map"][jid].get("axis", "?")
                if old.upper() != new_axis:
                    bone_map["joint_map"][jid]["axis"] = new_axis
                    changed_bm += 1

        with open(bone_map_path, "w", encoding="utf-8") as f:
            json.dump(bone_map, f, ensure_ascii=False, indent=2)
        print(f"  [OK] bone_map_{animal}.json 갱신: {bone_map_path} ({changed_bm}개 변경)")
    else:
        print(f"  [SKIP] bone_map 없음 (skeleton.json 만 갱신됨)")

    print(f"\n  다음 단계: generate_mappings.py --animal {animal}")


def main():
    parser = argparse.ArgumentParser(description="poses.json 기반 skeleton/bone_map axis 자동 갱신")
    parser.add_argument("--animal",  required=True, help="동물 이름 (예: spider)")
    parser.add_argument("--dry-run", action="store_true",
                        help="변경 내용만 출력, 파일 미수정")
    args = parser.parse_args()

    update_axes(args.animal, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
