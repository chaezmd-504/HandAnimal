"""
auto_chains.py
--------------
bone_map_{animal}.json 의 unity_path 계층 구조를 분석해
skeleton.json 에 chains 필드를 자동으로 추가한다.

체인 감지 규칙:
  unity_path 가 부모-자식 관계인 관절들을 이어서 체인으로 묶음.
  예) L.Leg → L.Leg/L.Bone.012 → L.Leg/L.Bone.012/L.Bone.013
      → chain: ["l_leg", "l_bone_012", "l_bone_013"]
  길이 2 이상, 경로 분기 없는 선형 구조만 체인으로 인정.

실행:
  python scripts/auto_chains.py --animal spider
  python scripts/auto_chains.py --animal spider --dry-run
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
SKEL_DIR   = os.path.join(PYTHON_DIR, "data", "animal_skeletons")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def detect_chains(path_map: dict[str, str]) -> list[list[str]]:
    """
    {joint_id: unity_path} 에서 체인을 자동 감지.

    반환: [ ["root", "mid", "tip"], ... ]  (길이 ≥ 2)
    """
    path_to_id = {v: k for k, v in path_map.items()}

    # 직접 부모 찾기: unity_path 에서 마지막 세그먼트 제거
    parent_of: dict[str, str] = {}
    for jid, path in path_map.items():
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            parent_id = path_to_id.get(parts[0])
            if parent_id is not None:
                parent_of[jid] = parent_id

    # 자식 목록 구성
    children_of: dict[str, list[str]] = {jid: [] for jid in path_map}
    for jid, pid in parent_of.items():
        children_of[pid].append(jid)

    def build_chain(start: str) -> list[str]:
        """start 부터 자식이 1개인 동안 이어나가며 체인 구성."""
        chain = [start]
        cur   = start
        while len(children_of[cur]) == 1:
            child = children_of[cur][0]
            chain.append(child)
            cur   = child
        return chain

    visited: set[str] = set()
    chains: list[list[str]] = []

    for jid in path_map:
        if jid in visited:
            continue
        # 체인 시작점: 부모가 없거나, 부모가 자식을 2개 이상 가짐
        parent = parent_of.get(jid)
        if parent is None or len(children_of[parent]) != 1:
            # 자신이 자식을 가지면 체인 출발
            if children_of[jid]:
                chain = build_chain(jid)
                if len(chain) >= 2:
                    chains.append(chain)
                visited.update(chain)

    return chains


def run(animal: str, dry_run: bool = False):
    skeleton_path = os.path.join(SKEL_DIR, f"{animal}.json")
    bone_map_path = os.path.join(SKEL_DIR, f"bone_map_{animal}.json")

    if not os.path.exists(skeleton_path):
        print(f"[ERROR] skeleton 없음: {skeleton_path}")
        sys.exit(1)
    if not os.path.exists(bone_map_path):
        print(f"[ERROR] bone_map 없음: {bone_map_path}")
        sys.exit(1)

    with open(skeleton_path, encoding="utf-8") as f:
        skeleton = json.load(f)
    with open(bone_map_path, encoding="utf-8") as f:
        bone_map = json.load(f)

    joint_map = bone_map.get("joint_map", {})
    path_map  = {jid: info["unity_path"] for jid, info in joint_map.items()
                 if "unity_path" in info}

    chains = detect_chains(path_map)

    print(f"\n[auto_chains] {animal.upper()} — {'DRY RUN' if dry_run else '적용'}")
    print(f"{'─'*50}")
    print(f"  감지된 체인: {len(chains)}개")
    for chain in chains:
        print(f"    {' → '.join(chain)}")

    existing = skeleton.get("chains", [])
    if existing:
        print(f"\n  기존 chains ({len(existing)}개) 덮어씁니다.")

    if dry_run:
        print("\n  --dry-run: 파일 미수정")
        return

    skeleton["chains"] = chains
    with open(skeleton_path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)
    print(f"\n  [OK] skeleton.json 갱신: {skeleton_path}")
    print(f"  다음 단계: parse_anim.py → update_axes.py → generate_mappings.py")


def main():
    parser = argparse.ArgumentParser(description="bone_map unity_path 기반 체인 자동 감지")
    parser.add_argument("--animal",  required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.animal, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
