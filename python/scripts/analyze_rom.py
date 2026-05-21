"""
analyze_rom.py
--------------
Unity .anim 파일을 파싱하여 각 본의 실제 ROM을 분석하고,
skeleton JSON의 min_angle / max_angle을 실측값으로 자동 갱신한다.

★ 세 가지 모드

  [analyze 모드] — Unity 본 경로 탐색 + 축별 ROM 출력 + template bone_map 생성
      python scripts/analyze_rom.py --mode analyze --anim_dir <path>
      python scripts/analyze_rom.py --mode analyze --anim_dir <path> --save_template

  [generate 모드] — bone_map 설정 기반으로 skeleton JSON 갱신
      python scripts/analyze_rom.py --mode generate --bone_map <config.json>

  [extract_poses 모드] — .anim 키프레임 → {animal}_poses.json 자동 생성 (Unity 수동 캡처 불필요)
      python scripts/analyze_rom.py --mode extract_poses --bone_map <config.json>
      python scripts/analyze_rom.py --mode extract_poses --bone_map <config.json> --threshold 8.0

★ bone_map 설정 파일 형식 (JSON):
    {
      "animal"        : "spider",
      "anim_dir"      : "../../unity/Assets/Spiders/Animations",
      "rest_anim"     : "Idle",
      "skeleton_path" : "../../data/animal_skeletons/spider.json",
      "output_path"   : "../../data/animal_skeletons/spider.json",
      "margin_deg"    : 5.0,          // 실측 ROM에 추가할 여유각 (단방향)
      "joint_map"     : {
        "leg_L1_base" : {"unity_path": "Spider/Bone/L.Leg",                         "axis": "Z"},
        "leg_L1_mid"  : {"unity_path": "Spider/Bone/L.Leg/L.Bone.012",              "axis": "X"},
        "leg_L1_tip"  : {"unity_path": "Spider/Bone/L.Leg/L.Bone.012/L.Bone.013",   "axis": "X"},
        ...
      }
    }

★ axis 선택 기준
    - 실제로 움직이는 축 = 해당 본의 'analyze' 결과에서 range가 가장 큰 축
    - skeleton.json의 axis 필드와 맞춰야 함 (AnimalController가 이 값을 사용)
"""

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict


# ──────────────────────────────────────────────────────────────
# 거미 기본 bone_map (analyze 결과 기반)
# 다른 동물 추가 시 bone_map JSON 파일을 별도 작성
# ──────────────────────────────────────────────────────────────
_SPIDER_BONE_MAP = {
    "leg_R1_base": {"unity_path": "Spider/Bone/R.Leg",                              "axis": "Z"},
    "leg_R1_mid":  {"unity_path": "Spider/Bone/R.Leg/R.Bone.012",                   "axis": "X"},
    "leg_R1_tip":  {"unity_path": "Spider/Bone/R.Leg/R.Bone.012/R.Bone.013",        "axis": "X"},
    "leg_R2_base": {"unity_path": "Spider/Bone/R.Leg.001",                          "axis": "Z"},
    "leg_R2_mid":  {"unity_path": "Spider/Bone/R.Leg.001/R.Bone.009",               "axis": "X"},
    "leg_R2_tip":  {"unity_path": "Spider/Bone/R.Leg.001/R.Bone.009/R.Bone.010",    "axis": "X"},
    "leg_R3_base": {"unity_path": "Spider/Bone/R.Leg.002",                          "axis": "Z"},
    "leg_R3_mid":  {"unity_path": "Spider/Bone/R.Leg.002/R.Bone.006",               "axis": "X"},
    "leg_R3_tip":  {"unity_path": "Spider/Bone/R.Leg.002/R.Bone.006/R.Bone.007",    "axis": "X"},
    "leg_R4_base": {"unity_path": "Spider/Bone/R.Leg.003",                          "axis": "Z"},
    "leg_R4_mid":  {"unity_path": "Spider/Bone/R.Leg.003/R.Bone.003",               "axis": "X"},
    "leg_R4_tip":  {"unity_path": "Spider/Bone/R.Leg.003/R.Bone.003/R.Bone.004",    "axis": "X"},
    "leg_L1_base": {"unity_path": "Spider/Bone/L.Leg",                              "axis": "Z"},
    "leg_L1_mid":  {"unity_path": "Spider/Bone/L.Leg/L.Bone.012",                   "axis": "X"},
    "leg_L1_tip":  {"unity_path": "Spider/Bone/L.Leg/L.Bone.012/L.Bone.013",        "axis": "X"},
    "leg_L2_base": {"unity_path": "Spider/Bone/L.Leg.001",                          "axis": "Z"},
    "leg_L2_mid":  {"unity_path": "Spider/Bone/L.Leg.001/L.Bone.009",               "axis": "X"},
    "leg_L2_tip":  {"unity_path": "Spider/Bone/L.Leg.001/L.Bone.009/L.Bone.010",    "axis": "X"},
    "leg_L3_base": {"unity_path": "Spider/Bone/L.Leg.002",                          "axis": "Z"},
    "leg_L3_mid":  {"unity_path": "Spider/Bone/L.Leg.002/L.Bone.006",               "axis": "X"},
    "leg_L3_tip":  {"unity_path": "Spider/Bone/L.Leg.002/L.Bone.006/L.Bone.007",    "axis": "X"},
    "leg_L4_base": {"unity_path": "Spider/Bone/L.Leg.003",                          "axis": "Z"},
    "leg_L4_mid":  {"unity_path": "Spider/Bone/L.Leg.003/L.Bone.003",               "axis": "X"},
    "leg_L4_tip":  {"unity_path": "Spider/Bone/L.Leg.003/L.Bone.003/L.Bone.004",    "axis": "X"},
}


# ──────────────────────────────────────────────────────────────
# 쿼터니언 유틸
# ──────────────────────────────────────────────────────────────

def _quat_mul(q1, q2):
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
    )


def _quat_inv(q):
    x, y, z, w = q
    n2 = x*x + y*y + z*z + w*w
    if n2 < 1e-10:
        return (0.0, 0.0, 0.0, 1.0)
    return (-x/n2, -y/n2, -z/n2, w/n2)


def _quat_to_euler_zxy(q):
    """쿼터니언 → (ex, ey, ez) Euler 각도 (degree), Unity ZXY RotationOrder."""
    x, y, z, w = q
    sinx = 2.0 * (w*x - y*z)
    sinx = max(-1.0, min(1.0, sinx))
    ex = math.degrees(math.asin(sinx))
    siny_cosx = 2.0 * (w*y + x*z)
    cosy_cosx = 1.0 - 2.0 * (x*x + y*y)
    ey = math.degrees(math.atan2(siny_cosx, cosy_cosx))
    sinz_cosx = 2.0 * (w*z + x*y)
    cosz_cosx = 1.0 - 2.0 * (x*x + z*z)
    ez = math.degrees(math.atan2(sinz_cosx, cosz_cosx))
    return ex, ey, ez


def _normalize_angle(a):
    while a > 180.0:
        a -= 360.0
    while a < -180.0:
        a += 360.0
    return a


# ──────────────────────────────────────────────────────────────
# .anim 파싱
# ──────────────────────────────────────────────────────────────

_RE_VALUE = re.compile(
    r'value:\s*\{x:\s*([-\d.e+]+),\s*y:\s*([-\d.e+]+),\s*z:\s*([-\d.e+]+),\s*w:\s*([-\d.e+]+)\}'
)
_RE_PATH  = re.compile(r'^\s*path:\s*(.+)$')
_RE_TIME  = re.compile(r'^\s*time:\s*([-\d.e+]+)')

_SKIP_KEYS = {
    'inSlope', 'outSlope', 'tangentMode', 'weightedMode',
    'inWeight', 'outWeight', 'm_PreInfinity', 'm_PostInfinity',
    'm_RotationOrder', 'serializedVersion',
}


def _parse_anim(filepath):
    """m_RotationCurves → {path: [(time, (x,y,z,w)), ...]}"""
    curves = defaultdict(list)
    with open(filepath, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    in_rot = False
    current_path = None
    pending_time = None

    for line in lines:
        if 'm_RotationCurves:' in line:
            in_rot = True
            continue

        if in_rot:
            stripped = line.strip()
            # 최상위 다른 섹션 감지 (들여쓰기 2칸 키워드)
            if re.match(r'  \w', line) and ':' in line:
                key = stripped.split(':')[0]
                if key not in _SKIP_KEYS and 'm_RotationCurves' not in line \
                        and not stripped.startswith('-') \
                        and 'curve' not in key and 'm_Curve' not in key \
                        and 'path' not in key and 'time' not in key and 'value' not in key:
                    in_rot = False

        if not in_rot:
            continue

        m = _RE_PATH.match(line)
        if m:
            current_path = m.group(1).strip()
            continue

        m = _RE_TIME.match(line)
        if m:
            pending_time = float(m.group(1))
            continue

        m = _RE_VALUE.search(line)
        if m and pending_time is not None and current_path is not None:
            q = (float(m.group(1)), float(m.group(2)),
                 float(m.group(3)), float(m.group(4)))
            curves[current_path].append((pending_time, q))
            pending_time = None

    return dict(curves)


# ──────────────────────────────────────────────────────────────
# 공통: 모든 .anim에서 본별 Euler 범위 수집
# ──────────────────────────────────────────────────────────────

def _collect_rom(anim_dir, rest_anim_name):
    """
    반환: {path: {'ex': [..], 'ey': [..], 'ez': [..], 'clips': set()}}
    """
    anim_files = [f for f in os.listdir(anim_dir) if f.endswith('.anim')]
    if not anim_files:
        print(f"[ERROR] .anim 파일 없음: {anim_dir}")
        sys.exit(1)

    # rest pose (Idle t=0)
    rest_file = os.path.join(anim_dir, rest_anim_name + '.anim')
    if not os.path.exists(rest_file):
        rest_file = os.path.join(anim_dir, anim_files[0])
        print(f"[WARN] {rest_anim_name}.anim 없음. {anim_files[0]} t=0 을 rest로 사용.\n")

    rest_curves = _parse_anim(rest_file)
    rest_pose = {}
    for path, kf in rest_curves.items():
        if kf:
            rest_pose[path] = sorted(kf, key=lambda k: k[0])[0][1]

    rom_data = defaultdict(lambda: {'ex': [], 'ey': [], 'ez': [], 'clips': set()})

    for fname in sorted(anim_files):
        clip = fname.replace('.anim', '')
        curves = _parse_anim(os.path.join(anim_dir, fname))
        for path, kf in curves.items():
            r_q = rest_pose.get(path)
            for _, q in kf:
                delta = _quat_mul(_quat_inv(r_q), q) if r_q else q
                ex, ey, ez = _quat_to_euler_zxy(delta)
                rom_data[path]['ex'].append(_normalize_angle(ex))
                rom_data[path]['ey'].append(_normalize_angle(ey))
                rom_data[path]['ez'].append(_normalize_angle(ez))
                rom_data[path]['clips'].add(clip)

    return dict(rom_data), anim_files


def _primary_axis(d):
    """세 축 중 range가 가장 큰 축 이름 반환."""
    ranges = {
        'X': max(d['ex']) - min(d['ex']),
        'Y': max(d['ey']) - min(d['ey']),
        'Z': max(d['ez']) - min(d['ez']),
    }
    return max(ranges, key=ranges.get), ranges


def _get_range(d, axis):
    key = {'X': 'ex', 'Y': 'ey', 'Z': 'ez'}[axis.upper()]
    return min(d[key]), max(d[key])


# ──────────────────────────────────────────────────────────────
# 모드 1: analyze
# ──────────────────────────────────────────────────────────────

def mode_analyze(anim_dir, rest_anim, save_template, template_out):
    print(f"[분석] 폴더: {anim_dir}  |  rest: {rest_anim}.anim\n")
    rom_data, anim_files = _collect_rom(anim_dir, rest_anim)
    print(f"클립: {anim_files}\n")

    print("=" * 80)
    print(f"{'본(path)':<45} {'주 축':>4}  {'X range':>14} {'Y range':>14} {'Z range':>14}")
    print("=" * 80)

    for path in sorted(rom_data.keys()):
        d = rom_data[path]
        primary, ranges = _primary_axis(d)
        x_r = f"{min(d['ex']):+.1f}~{max(d['ex']):+.1f}"
        y_r = f"{min(d['ey']):+.1f}~{max(d['ey']):+.1f}"
        z_r = f"{min(d['ez']):+.1f}~{max(d['ez']):+.1f}"
        flag = " ★" if ranges[primary] > 5 else ""
        print(f"{path:<45} [{primary}]{flag:2}  {x_r:>14} {y_r:>14} {z_r:>14}")

    print("=" * 80)
    print(f"\n총 {len(rom_data)}개 본 분석 완료.")
    print("\n※ 주 축(★): range > 5° — skeleton.json 'axis' 필드와 대조하세요.\n")

    # bone_map 템플릿 저장
    if save_template:
        template = {}
        for path in sorted(rom_data.keys()):
            d = rom_data[path]
            primary, _ = _primary_axis(d)
            bone_name = path.split('/')[-1]
            template[bone_name] = {
                "unity_path": path,
                "axis": primary,
                "_X_range": f"{min(d['ex']):+.1f}~{max(d['ex']):+.1f}",
                "_Y_range": f"{min(d['ey']):+.1f}~{max(d['ey']):+.1f}",
                "_Z_range": f"{min(d['ez']):+.1f}~{max(d['ez']):+.1f}",
            }

        config = {
            "_usage": "joint_map의 key를 skeleton.json 의 id와 맞추고 _로 시작하는 필드는 삭제하세요.",
            "animal": "???",
            "anim_dir": anim_dir,
            "rest_anim": rest_anim,
            "skeleton_path": "data/animal_skeletons/???.json",
            "output_path":   "data/animal_skeletons/???.json",
            "margin_deg": 5.0,
            "joint_map": template,
        }
        with open(template_out, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"[저장] bone_map 템플릿 → {template_out}")
        print("  → joint_map 의 key를 skeleton.json id 값으로 수정한 뒤 --mode generate 를 실행하세요.\n")


# ──────────────────────────────────────────────────────────────
# 모드 2: generate — skeleton JSON min/max_angle 자동 갱신
# ──────────────────────────────────────────────────────────────

def mode_generate(bone_map_path):
    with open(bone_map_path, encoding='utf-8') as f:
        cfg = json.load(f)

    # 설정 읽기
    cfg_dir     = os.path.dirname(os.path.abspath(bone_map_path))
    anim_dir    = os.path.normpath(os.path.join(cfg_dir, cfg['anim_dir']))
    rest_anim   = cfg.get('rest_anim', 'Idle')
    skel_path   = os.path.normpath(os.path.join(cfg_dir, cfg['skeleton_path']))
    out_path    = os.path.normpath(os.path.join(cfg_dir, cfg.get('output_path', skel_path)))
    margin      = float(cfg.get('margin_deg', 5.0))
    joint_map   = cfg['joint_map']

    print(f"[generate] 동물     : {cfg.get('animal', '???')}")
    print(f"           anim_dir : {anim_dir}")
    print(f"           skeleton : {skel_path}")
    print(f"           출력     : {out_path}")
    print(f"           margin   : ±{margin}°\n")

    # .anim 파싱
    rom_data, anim_files = _collect_rom(anim_dir, rest_anim)
    print(f"클립: {anim_files}\n")

    # skeleton JSON 로드
    with open(skel_path, encoding='utf-8') as f:
        skeleton = json.load(f)

    # joint_id → rom_data path 역매핑
    path_index = {v['unity_path']: k for k, v in joint_map.items()}

    # 결과 테이블 출력 + skeleton 갱신
    print("=" * 90)
    print(f"{'관절 id':<20} {'Unity path':<45} {'축':>3}  {'실측 ROM':>16} {'적용 ROM':>16} {'변경'}")
    print("=" * 90)

    updated = 0
    warnings_list = []

    for joint in skeleton['joints']:
        jid = joint['id']
        if jid not in joint_map:
            print(f"  [SKIP] {jid:20s} — bone_map 에 없음")
            continue

        entry    = joint_map[jid]
        upath    = entry['unity_path']
        axis     = entry['axis'].upper()

        old_min  = joint.get('min_angle', 0.0)
        old_max  = joint.get('max_angle', 0.0)

        if upath not in rom_data:
            print(f"  [WARN] {jid:20s} - Unity 본 없음: {upath}")
            warnings_list.append(f"{jid}: unity_path '{upath}' 를 .anim 에서 찾지 못했습니다.")
            continue

        d = rom_data[upath]
        meas_min, meas_max = _get_range(d, axis)

        # 실측에 margin 추가
        new_min = round(meas_min - margin, 1)
        new_max = round(meas_max + margin, 1)

        # 주 축과 지정 축 불일치 경고
        primary, _ = _primary_axis(d)
        axis_warn = ""
        if primary != axis:
            axis_warn = f" [!주축={primary}]"
            warnings_list.append(
                f"{jid}: 지정 axis={axis} 이지만 실제 주 움직임 축은 {primary} 입니다."
            )

        changed = (new_min != old_min or new_max != old_max)
        marker  = "+" if changed else "="
        meas_str = f"{meas_min:+.1f}°~{meas_max:+.1f}°"
        new_str  = f"{new_min:+.1f}°~{new_max:+.1f}°"

        print(f"  {jid:<20} {upath:<45} [{axis}]  {meas_str:>16} {new_str:>16}  {marker}{axis_warn}")

        joint['min_angle'] = new_min
        joint['max_angle'] = new_max
        joint['axis']      = axis
        if changed:
            updated += 1

    print("=" * 90)

    # 갱신된 skeleton 저장
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)

    print(f"\n[완료] {updated}개 관절 갱신 → {out_path}")

    if warnings_list:
        print("\n[경고]")
        for w in warnings_list:
            print(f"  - {w}")

    print("\n[다음 단계]")
    print("  1. [!주축=?] 항목의 axis 필드가 skeleton.json 및 AnimalController 설정과 일치하는지 확인")
    print("  2. generate_mappings.py 재실행 -> 새 ROM 기반 매핑 JSON 재생성")
    print("  3. python scripts/show_mapping.py 로 결과 확인")


# ──────────────────────────────────────────────────────────────
# 모드 3: extract_poses — .anim 키프레임 → {animal}_poses.json
# ──────────────────────────────────────────────────────────────

def _nearest_quat(keyframes, t):
    """시간 t에서 가장 가까운 키프레임의 쿼터니언 반환."""
    best_q, best_dt = keyframes[0][1], abs(keyframes[0][0] - t)
    for kf_t, kf_q in keyframes[1:]:
        dt = abs(kf_t - t)
        if dt < best_dt:
            best_dt, best_q = dt, kf_q
    return best_q


def _pose_distance(p1, p2):
    """두 포즈의 최대 관절 각도 차이 (degree)."""
    keys = set(p1) | set(p2)
    return max(abs(p1.get(k, 0.0) - p2.get(k, 0.0)) for k in keys)


def _deduplicate_poses(poses, threshold):
    """서로 threshold° 이상 다른 포즈만 남긴다."""
    unique = []
    for pose in poses:
        if all(_pose_distance(pose, u) >= threshold for u in unique):
            unique.append(pose)
    return unique


def mode_extract_poses(bone_map_path, threshold=5.0):
    """
    bone_map 설정의 .anim 키프레임을 읽어 포즈 집합 P를 자동 추출.
    결과: data/animal_skeletons/{animal}_poses.json
    """
    with open(bone_map_path, encoding='utf-8') as f:
        cfg = json.load(f)

    cfg_dir    = os.path.dirname(os.path.abspath(bone_map_path))
    anim_dir   = os.path.normpath(os.path.join(cfg_dir, cfg['anim_dir']))
    rest_anim  = cfg.get('rest_anim', 'Idle')
    animal     = cfg.get('animal', 'unknown')
    joint_map  = cfg['joint_map']

    # poses 출력 경로 — skeleton_path 옆에 {animal}_poses.json
    skel_path  = os.path.normpath(os.path.join(cfg_dir, cfg.get('skeleton_path', '')))
    poses_out  = os.path.join(os.path.dirname(skel_path), f"{animal}_poses.json")
    if 'poses_output_path' in cfg:
        poses_out = os.path.normpath(os.path.join(cfg_dir, cfg['poses_output_path']))

    print(f"[extract_poses] 동물     : {animal}")
    print(f"                anim_dir : {anim_dir}")
    print(f"                출력     : {poses_out}")
    print(f"                threshold: {threshold}°  (이 값 이상 달라야 별도 포즈로 인정)\n")

    # rest 포즈 로드
    anim_files = sorted(f for f in os.listdir(anim_dir) if f.endswith('.anim'))
    rest_file  = os.path.join(anim_dir, rest_anim + '.anim')
    if not os.path.exists(rest_file):
        rest_file = os.path.join(anim_dir, anim_files[0])
        print(f"[WARN] {rest_anim}.anim 없음 → {anim_files[0]} t=0 을 rest로 사용.\n")

    rest_curves = _parse_anim(rest_file)
    rest_pose   = {
        path: sorted(kf, key=lambda k: k[0])[0][1]
        for path, kf in rest_curves.items() if kf
    }

    all_poses = []

    for fname in anim_files:
        clip   = fname.replace('.anim', '')
        curves = _parse_anim(os.path.join(anim_dir, fname))

        # 이 클립에서 bone_map에 속한 joint들의 모든 타임스탬프 수집
        times = set()
        for entry in joint_map.values():
            upath = entry['unity_path']
            if upath in curves:
                times.update(t for t, _ in curves[upath])

        if not times:
            print(f"  [SKIP] {clip}: bone_map 관절 키프레임 없음")
            continue

        clip_poses = 0
        for t in sorted(times):
            pose = {}
            for jid, entry in joint_map.items():
                upath = entry['unity_path']
                axis  = entry['axis'].upper()

                if upath not in curves or not curves[upath]:
                    pose[jid] = 0.0
                    continue

                q   = _nearest_quat(curves[upath], t)
                r_q = rest_pose.get(upath)
                delta = _quat_mul(_quat_inv(r_q), q) if r_q else q

                ex, ey, ez = _quat_to_euler_zxy(delta)
                raw = {'X': ex, 'Y': ey, 'Z': ez}[axis]
                pose[jid] = round(_normalize_angle(raw), 1)

            all_poses.append(pose)
            clip_poses += 1

        print(f"  {clip}: {clip_poses}개 키프레임")

    # 중복 제거
    before = len(all_poses)
    unique = _deduplicate_poses(all_poses, threshold)
    print(f"\n[중복 제거] {before}개 → {len(unique)}개 (threshold={threshold}°)")

    # 저장
    os.makedirs(os.path.dirname(poses_out) or '.', exist_ok=True)
    with open(poses_out, 'w', encoding='utf-8') as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(f"[완료] {len(unique)}개 포즈 → {poses_out}")
    print("\n[다음 단계]")
    print("  python scripts/generate_mappings.py  → 새 포즈 기반 매핑 JSON 재생성")


# ──────────────────────────────────────────────────────────────
# 모드 4: dump_spider_map — 내장 거미 bone_map 파일로 저장
# ──────────────────────────────────────────────────────────────

def mode_dump_spider(out_path, anim_dir, skeleton_path):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_dir = os.path.dirname(script_dir)

    cfg = {
        "_usage": "joint_map key = skeleton.json joint id. _로 시작 필드는 참고용.",
        "animal":        "spider",
        "anim_dir":      anim_dir or os.path.relpath(
            os.path.join(python_dir, '..', 'unity', 'Assets', 'Spiders', 'Animations'),
            os.path.dirname(os.path.abspath(out_path))
        ),
        "rest_anim":     "Idle",
        "skeleton_path": skeleton_path or os.path.relpath(
            os.path.join(python_dir, 'data', 'animal_skeletons', 'spider.json'),
            os.path.dirname(os.path.abspath(out_path))
        ),
        "output_path":   skeleton_path or os.path.relpath(
            os.path.join(python_dir, 'data', 'animal_skeletons', 'spider.json'),
            os.path.dirname(os.path.abspath(out_path))
        ),
        "margin_deg":    5.0,
        "joint_map":     _SPIDER_BONE_MAP,
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[저장] 거미 bone_map → {out_path}")
    print("  수정 후: python scripts/analyze_rom.py --mode generate --bone_map <위 파일>")


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

def main():
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    python_dir   = os.path.dirname(script_dir)
    default_anim = os.path.normpath(
        os.path.join(python_dir, '..', 'unity', 'Assets', 'Spiders', 'Animations')
    )

    parser = argparse.ArgumentParser(
        description="Unity .anim ROM 분석 & skeleton JSON 자동 갱신 & 포즈 추출",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  # 1. 본 구조 탐색 + 템플릿 생성\n"
            "  python scripts/analyze_rom.py --mode analyze --save_template\n\n"
            "  # 2. 거미 기본 bone_map 저장\n"
            "  python scripts/analyze_rom.py --mode dump_spider --out bone_map_spider.json\n\n"
            "  # 3. skeleton JSON ROM 갱신\n"
            "  python scripts/analyze_rom.py --mode generate --bone_map bone_map_spider.json\n\n"
            "  # 4. .anim 키프레임 → {animal}_poses.json 자동 추출 (Unity 수동 캡처 불필요)\n"
            "  python scripts/analyze_rom.py --mode extract_poses --bone_map bone_map_spider.json\n"
            "  python scripts/analyze_rom.py --mode extract_poses --bone_map bone_map_spider.json --threshold 8.0\n"
        ),
    )
    parser.add_argument(
        '--mode',
        choices=['analyze', 'generate', 'dump_spider', 'extract_poses'],
        default='analyze',
        help='실행 모드 (기본: analyze)',
    )
    # analyze 옵션
    parser.add_argument('--anim_dir',   default=default_anim,  help='애니메이션 폴더')
    parser.add_argument('--rest_anim',  default='Idle',         help='rest pose 기준 클립명')
    parser.add_argument('--save_template', action='store_true', help='bone_map 템플릿 JSON 저장')
    parser.add_argument('--template_out', default='bone_map_template.json',
                        help='템플릿 저장 경로 (기본: bone_map_template.json)')
    # generate / extract_poses 옵션
    parser.add_argument('--bone_map',   default='',             help='bone_map 설정 JSON 경로')
    parser.add_argument('--threshold',  type=float, default=5.0,
                        help='[extract_poses] 포즈 중복 제거 임계값 degree (기본: 5.0)')
    # dump_spider 옵션
    parser.add_argument('--out',        default='bone_map_spider.json',
                        help='dump_spider 출력 경로')
    parser.add_argument('--skeleton',   default='',             help='dump_spider: skeleton 경로 힌트')

    args = parser.parse_args()

    if args.mode == 'analyze':
        mode_analyze(args.anim_dir, args.rest_anim, args.save_template, args.template_out)

    elif args.mode == 'generate':
        if not args.bone_map:
            parser.error("--mode generate 에는 --bone_map 이 필요합니다.")
        if not os.path.exists(args.bone_map):
            parser.error(f"bone_map 파일 없음: {args.bone_map}")
        mode_generate(args.bone_map)

    elif args.mode == 'extract_poses':
        if not args.bone_map:
            parser.error("--mode extract_poses 에는 --bone_map 이 필요합니다.")
        if not os.path.exists(args.bone_map):
            parser.error(f"bone_map 파일 없음: {args.bone_map}")
        mode_extract_poses(args.bone_map, threshold=args.threshold)

    elif args.mode == 'dump_spider':
        mode_dump_spider(args.out, args.anim_dir if args.anim_dir != default_anim else '',
                         args.skeleton)


if __name__ == '__main__':
    main()
