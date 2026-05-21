"""
show_mapping.py
---------------
매핑 JSON을 사람이 읽기 쉬운 형태로 출력한다.
어떤 손 자세/동작이 어떤 아바타 관절을 어떻게 움직이는지 한눈에 확인.

사용법:
    python scripts/show_mapping.py [--animal spider]
    python scripts/show_mapping.py --animal fish
    python scripts/show_mapping.py --all
"""

import argparse
import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR   = os.path.dirname(SCRIPT_DIR)
MAPPINGS_DIR = os.path.join(PYTHON_DIR, "data", "mappings")
SKELETONS_DIR = os.path.join(PYTHON_DIR, "data", "animal_skeletons")

ANIMALS = ["spider", "butterfly", "fish", "octopus", "snake"]

# ──────────────────────────────────────────────────────────────
# 손 DOF 설명 (한국어)
# ──────────────────────────────────────────────────────────────
DOF_INFO = {
    # name: (한국어 동작, 양수 방향 설명, 음수 방향 설명, range_str)
    "wrist_flex":  ("손목 앞뒤 굽힘",   "손등 위로 꺾음(+70)",  "손바닥 아래로 꺾음(-70)", "-70~+70"),
    "wrist_dev":   ("손목 좌우 편위",   "엄지 방향 편위(+25)",  "소지 방향 편위(-25)",     "-25~+25"),
    "wrist_rot":   ("전완 회전",        "시계 방향 회전(+90)",  "반시계 방향 회전(-90)",   "-90~+90"),
    "thumb_cmc":   ("엄지 CMC 굽힘",   "굽힘(+60)",            "신전(0)",                 "0~+60"),
    "thumb_abd":   ("엄지 벌림",        "검지에서 멀어짐(+70)", "검지에 붙음(0)",           "0~+70"),
    "thumb_mcp":   ("엄지 MCP 굽힘",   "굽힘(+60)",            "신전(0)",                 "0~+60"),
    "thumb_ip":    ("엄지 끝마디 굽힘", "굽힘(+80)",            "신전(0)",                 "0~+80"),
    "index_mcp":   ("검지 첫마디 굽힘", "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "index_pip":   ("검지 중간마디",    "굽힘(+110)",           "신전(0)",                 "0~+110"),
    "index_dip":   ("검지 끝마디",      "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "middle_mcp":  ("중지 첫마디 굽힘", "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "middle_pip":  ("중지 중간마디",    "굽힘(+110)",           "신전(0)",                 "0~+110"),
    "middle_dip":  ("중지 끝마디",      "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "ring_mcp":    ("약지 첫마디 굽힘", "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "ring_pip":    ("약지 중간마디",    "굽힘(+110)",           "신전(0)",                 "0~+110"),
    "ring_dip":    ("약지 끝마디",      "굽힘(+90)",            "신전(0)",                 "0~+90"),
    "pinky_cmc":   ("소지 손바닥 굽힘", "손바닥 오므림(+30)",    "평평(0)",                 "0~+30"),
    "pinky_mcp":   ("소지 첫마디 굽힘", "굽힘(+80)",            "신전(0)",                 "0~+80"),
    "pinky_pip":   ("소지 중간마디",    "굽힘(+100)",           "신전(0)",                 "0~+100"),
    "pinky_dip":   ("소지 끝마디",      "굽힘(+80)",            "신전(0)",                 "0~+80"),
}


def load_skeleton(animal: str) -> dict:
    path = os.path.join(SKELETONS_DIR, f"{animal}.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_hand_setup_guide():
    print("""
  =========================================================
  [기본 자세] - 모든 동작의 출발점
  =========================================================

    누군가한테 "잠깐만요" 할 때 손바닥 내미는 자세 (STOP 제스처)

      나         웹캠
      |   --->   [cam]
     팔
      |
    손목
      |
    [####]   <- 손바닥이 웹캠을 향함
    |||||    <- 손가락이 천장을 향함

    - 팔꿈치를 약간 구부려서 손이 가슴 높이에 오도록
    - 손목까지 화면 안에 들어오게 (손목 잘리면 인식 안 됨)
    - 웹캠에서 40~60cm 거리 권장

  =========================================================
  [손목 앞뒤 꺾기] wrist_flex  (-70 ~ +70도)
  =========================================================

    기본 자세에서 손목만 꺾는다. 팔은 움직이지 않음.

    +70도: 손등이 자기 얼굴을 향하도록 손목을 뒤로 꺾기
           (손가락이 웹캠 쪽을 가리키게 됨)

            [####]
            |||||
              \
             손목  <- 손목이 꺾임

    -70도: 손바닥이 자기 얼굴을 향하도록 손목을 앞으로 꺾기
           (손가락이 바닥을 가리키게 됨)

             손목  <- 손목이 꺾임
              /
            |||||
            [####]

  =========================================================
  [손목 좌우 기울이기] wrist_dev  (-25 ~ +25도)
  =========================================================

    기본 자세에서 손목을 좌우로 기울인다.

    +25도: 엄지손가락 방향으로 손목 기울이기 (오른손 기준: 오른쪽으로)
           손가락들이 오른쪽 위 대각선을 가리킴

    -25도: 새끼손가락 방향으로 손목 기울이기 (오른손 기준: 왼쪽으로)
           손가락들이 왼쪽 위 대각선을 가리킴

  =========================================================
  [전완 회전] wrist_rot  (-90 ~ +90도)
  =========================================================

    팔꿈치를 고정한 채 팔뚝을 비튼다. 손바닥은 계속 웹캠을 향함.

    +90도: 시계 방향으로 돌리기 -> 엄지가 아래, 새끼가 위
           (손을 오른쪽으로 90도 돌린 모양)

    0도:   엄지가 오른쪽, 새끼가 왼쪽 (기본)

    -90도: 반시계 방향으로 돌리기 -> 엄지가 위, 새끼가 아래
           (손을 왼쪽으로 90도 돌린 모양)

  =========================================================
  [손가락 굽히기] index/middle/ring/pinky  (0 ~ 90~110도)
  =========================================================

    기본 자세에서 손가락을 구부린다.

    0도:   손가락 완전히 펼침 (기본 자세)
    90도:  직각으로 구부림
    최대:  완전히 주먹 쥐기

    MCP (첫마디) -> PIP (중간마디) -> DIP (끝마디) 순서로 구부러짐
    세 마디가 비율에 따라 자동 분배됨 (31% : 47% : 22%)
""")


def show_animal(animal: str):
    path = os.path.join(MAPPINGS_DIR, f"{animal}_mapping.json")
    if not os.path.exists(path):
        print(f"[ERROR] 매핑 파일 없음: {path}")
        return

    with open(path, encoding="utf-8") as f:
        m = json.load(f)

    skeleton = load_skeleton(animal)
    joint_rom = {j["id"]: (j["min_angle"], j["max_angle"])
                 for j in skeleton.get("joints", [])}
    mode = m.get("mode", "unilateral")
    ref_H = m.get("reference_pose_H", {})
    ref_A = m.get("reference_pose_A", {})

    print(f"\n{'='*70}")
    print(f"  {animal.upper()} ({mode})")
    print(f"{'='*70}")

    # 손별로 분리
    if mode == "bilateral":
        grouped = {"left": [], "right": []}
        for jid, info in m["mapping"].items():
            grouped[info.get("hand", "right")].append((jid, info))
        sides = [("왼손 (Left)", "left", grouped["left"]),
                 ("오른손 (Right)", "right", grouped["right"])]
    else:
        sides = [("단일 손", None, list(m["mapping"].items()))]

    for side_label, side_key, entries in sides:
        if not entries:
            continue

        print(f"\n  ── {side_label} ────────────────────────────────────────")
        print(f"  {'아바타 관절':18s}  {'손 동작':18s}  {'움직임 설명':30s}  scale")
        print(f"  {'-'*78}")

        for jid, info in sorted(entries):
            dof_name = info["hand_dof_name"]
            scale    = info["scale_factor"]
            a_min, a_max = joint_rom.get(jid, (0, 0))
            a_neutral    = ref_A.get(jid, 0.0)

            dof_label, pos_desc, neg_desc, rng = DOF_INFO.get(
                dof_name, (dof_name, "+방향", "-방향", "?")
            )

            # 아바타 관절도 + 방향이 어떤 의미인지 (축 정보)
            axis = next((j.get("axis", "?") for j in skeleton.get("joints", [])
                         if j["id"] == jid), "?")
            a_desc = f"(축:{axis}, {a_min:.0f}~{a_max:.0f}deg, 중립:{a_neutral:.0f})"

            print(f"  {jid:18s}  {dof_label:18s}  {pos_desc:30s}  x{scale:.3f}")
            print(f"  {'':18s}  {'':18s}  {a_desc}")
            print()

        # 기준 포즈 (이 손 각도일 때 아바타 중립)
        print(f"  [기준 포즈 - {side_label}이 이 각도일 때 아바타 중립]")
        rh_dict = ref_H.get(side_key, ref_H) if side_key else ref_H
        mapped_dofs = {info["hand_dof_name"] for _, info in entries}
        used = {k: v for k, v in rh_dict.items() if k in mapped_dofs}

        wrist_parts  = [f"{k}={v:+.1f}deg" for k, v in used.items() if k.startswith("wrist_")]
        finger_parts = [f"{k}={v:+.1f}deg" for k, v in used.items() if not k.startswith("wrist_")]
        if wrist_parts:
            print(f"    손목: {', '.join(wrist_parts)}")
        if finger_parts:
            print(f"    손가락: {', '.join(finger_parts)}")

    print()


def print_wrist_dof_guide():
    print("""
  ── 손목 DOF 상세 가이드 ──────────────────────────────────────

  wrist_flex  (손목 앞뒤 굽힘, -70~+70deg)
    +70deg : 손등이 위로 가도록 손목을 뒤로 꺾음 (dorsiflexion)
    0      : 손목 중립 (손이 팔과 일직선)
    -70deg : 손바닥이 위로 가도록 손목을 앞으로 꺾음 (palmarflexion)
    계산법 : 손바닥 법선 벡터의 Y 성분

  wrist_dev   (손목 좌우 편위, -25~+25deg)
    +25deg : 엄지 방향으로 손목 기울임 (radial deviation)
    0      : 중립
    -25deg : 소지 방향으로 손목 기울임 (ulnar deviation)
    계산법 : 손바닥 법선 벡터의 X 성분

  wrist_rot   (전완 회전, -90~+90deg)
    +90deg : 시계 방향으로 전완 돌림 (pronation)
    0      : 중립 (손바닥이 카메라 정면)
    -90deg : 반시계 방향으로 전완 돌림 (supination)
    계산법 : 검지MCP-소지MCP 벡터의 이미지 평면 각도
""")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--animal", default="spider", choices=ANIMALS)
    p.add_argument("--all", action="store_true", help="모든 동물 출력")
    p.add_argument("--guide", action="store_true", help="손목 DOF 상세 가이드 출력")
    args = p.parse_args()

    print_hand_setup_guide()

    if args.guide:
        print_wrist_dof_guide()

    if args.all:
        for a in ANIMALS:
            show_animal(a)
    else:
        show_animal(args.animal)


if __name__ == "__main__":
    main()
