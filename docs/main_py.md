# main.py — 전체 파이프라인 진입점

## 개요

웹캠 → 손 추적 → DOF 각도 계산 → 매핑 → WebSocket → Unity 순서로 동작하는 메인 루프.  
`--mapping blend` 모드가 현재 권장 모드이며, 연속 제어(direct)와 액션 애니메이션(sequential) 두 가지를 동적으로 블렌딩한다.

---

## 실행 방법

```bash
conda activate capstone_env
python main.py --animal spider --mapping blend --wrist-death 40
```

### 주요 인수

| 인수 | 기본값 | 설명 |
|------|--------|------|
| `--animal` | `spider` | 동물 선택 |
| `--mapping` | `keyframe` | `keyframe` / `direct` / `blend` |
| `--temperature` | `8.0` | keyframe softmax 온도 |
| `--threshold` | `1.0` | LDA 트리거 민감도 (낮을수록 예민) |
| `--cursor-speed` | `0.4` | blend 모드 프레임당 cursor 진행 속도 |
| `--action-anims` | `Attack1,Attack2,Death` | action으로 처리할 애니메이션 목록 |
| `--wrist-death` | `None` | 설정 시 Death를 LDA 대신 wrist_rot 변화량으로 감지 |
| `--dist-log` | `0` | N프레임마다 anim 거리 출력 (0=비활성) |

---

## 매핑 모드 3가지

### 1. `direct` 모드
```
손 DOF → scale_factor → 관절 각도
```
- `MappingEngine.transform_clamped()` 사용
- 손 움직임이 즉각 반영되지만 wobbly(떨림)함
- float → xyz dict 변환: `_float_joints_to_xyz()` 호출

### 2. `keyframe` 모드
```
손 포즈 → 각 키프레임과 L2 거리 → softmax 가중치 → 키프레임 블렌딩
```
- `KeyframeMappingEngine.transform_bilateral()` 사용
- 자연스럽지만 동물 고유 포즈들만 표현 가능

### 3. `blend` 모드 (권장)
두 엔진을 동시에 사용하며 상태머신으로 전환.

```
[continuous 상태]
  joints_d = MappingEngine.transform_clamped()   ← direct 결과
  alpha = 0
  joints = joints_d

[action 상태]
  joints_kf = engine.get_sequential_pose(anim, cursor)  ← 애니메이션 키프레임
  alpha = sin(cursor/total * π)  또는 sin(min(t,0.5)*π)  (Death)
  joints[jid] = (1-alpha)*joints_d[jid] + alpha*joints_kf[jid]
```

---

## blend 모드 상태머신

```
          ┌──────────────────────────────────────────┐
          │            continuous                     │
          │  alpha=0, joints = joints_d               │
          └──────────────────┬───────────────────────┘
                             │ LDA 트리거 or wrist_death
                             ▼
          ┌──────────────────────────────────────────┐
          │              action                       │
          │  cursor += cursor_speed (매 프레임)        │
          │  alpha = sin curve                        │
          │  joints = blend(joints_d, joints_kf)      │
          └──────────────────┬───────────────────────┘
                             │ cursor >= frame_count
                             ▼
                     cooldown (45프레임, ~1.5초)
                             │
                             ▼
                         continuous
```

### 상태 변수

```python
_anim_state      = "continuous"   # "continuous" | "action"
_action_anim     = None           # 현재 재생 중인 anim 이름
_cursor          = 0.0            # 애니메이션 재생 위치 (프레임 단위 float)
_cooldown_frames = 0              # action 완료 후 재트리거 방지
```

---

## Death 트리거

### LDA 방식 (기본)
`--wrist-death` 미설정 시 Death도 LDA로 감지.

### wrist_rot 방식 (`--wrist-death N`)
```python
_delta_wr_r = abs(wrist_rot_right - ref_wrist_rot_right)
if _delta_wr_r >= args.wrist_death:
    → action: Death
```
- **오른손만** 사용 (왼손 센서 값 불안정)
- `N`은 각도(°) 단위. 권장값: 40~60

### Death alpha 커브
```python
# 일반 action: sin(cursor/total * π)  → 0에서 시작, 1.0 피크 후 다시 0으로 복귀
# Death:       sin(min(cursor/total, 0.5) * π) → 0에서 시작, 1.0 도달 후 유지
```
Death는 뒤집힌 상태를 끝까지 유지해야 하므로 후반부 복귀를 막음.

---

## LDA 트리거 (Attack1/2)

### 파일 로드
```python
_lda_path = f"{MAPPINGS_DIR}/{animal}_triggers_lda.json"
```
구조:
```json
{
  "left_dof_indices": [...],
  "right_dof_indices": [...],
  "triggers": {
    "Attack1": {
      "w_left": [...], "w_right": [...],
      "separation": 31.96,
      "direction": "positive"
    }
  }
}
```

### 임계값 계산
```python
_thr = separation * 0.5 * _ATTACK_THRESHOLD
# separation=31.96, threshold=1.0 → thr=15.98
```

### 스코어 계산
```python
delta_l = h_left  - ref_l    # 현재 손 - 캘리브레이션 기준
delta_r = h_right - ref_r
score = w_left[l_idx] @ delta_l[l_idx] + w_right[r_idx] @ delta_r[r_idx]
if score >= thr: → action
```

---

## body_mapping (bone/bodyik_001/atack1)

`spider_body_mapping.json` 로드 → continuous 모드에서 이 관절들에 **0을 명시 전송**.  
(이전엔 direct 매핑에서 wrist DOF가 body 관절로 직결되어 손목 움직임에 몸통이 좌우로 돌아가던 버그 수정)

```python
if _body_mapping:
    for _bj in _body_mapping:
        if _bj not in joints:          # action 모드에서 keyframe이 이미 값 채운 경우 덮어쓰지 않음
            joints[_bj] = {"x":0, "y":0, "z":0}
```

action(Death) 모드에서는 `joints_kf`에 bone/bodyik_001 값이 포함되므로 덮어쓰지 않음.  
`atack1`은 `joints_kf.pop("atack1", None)` 으로 keyframe에서 항상 제거.

---

## 3° Deadzone (continuous 모드)

```python
_DEAD = 3.0
for _jid in joints_d:
    joints_d[_jid] = {ax: (v if abs(v) >= _DEAD else 0.0) for ax, v in _v.items()}
```
캘리브레이션 포즈에서 미세한 손 떨림이 다리를 흔드는 것을 방지.

---

## EMA 스무딩

```python
_EMA_ALPHA = 0.55
# dof_ema = alpha * current + (1-alpha) * prev
```
낮을수록 더 강한 스무딩. 0.55는 빠른 반응과 스무딩의 균형.

---

## 데이터 흐름 상세

```
웹캠 프레임
    │
    ▼
MediaPipe HandLandmarker
    │ hand landmarks (21개 점)
    ▼
compute_dof_angles()
    │ {"left": {wrist_flex, wrist_dev, ..., pinky_dip}, "right": {...}}
    │ 총 20개 DOF × 양손
    ▼
OcclusionHandler
    │ 손이 안 보일 때 이전 값 유지
    ▼
EMA 스무딩
    │
    ▼
blend 모드 매핑
    ├─ MappingEngine.transform_clamped()  → joints_d (float)
    │      → _float_joints_to_xyz()       → joints_d (xyz dict)
    │      → 3° deadzone 적용
    │
    ├─ 상태머신 판단 (LDA / wrist_death)
    │
    └─ action 시: KeyframeMappingEngine.get_sequential_pose()
           → Euler 최단경로 보간
           → alpha 블렌딩
    │
    ▼
body joints 0 전송 / keyframe 값 유지
    │
    ▼
WebSocketServer.send_joints(joints)
    │ JSON {"bone": {"x":0,"y":0,"z":171}, ...}
    ▼
Unity AnimalController.ApplyJoints()
```
