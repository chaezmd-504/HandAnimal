# 전체 파이프라인 개요

## 시스템 구성

```
[웹캠]
  │
  ▼
[Python main.py]
  ├── MediaPipe HandLandmarker   손 추적
  ├── compute_dof_angles()       DOF 각도 계산
  ├── OcclusionHandler           손 가림 처리
  ├── EMA 스무딩                 떨림 감소
  ├── MappingEngine (direct)     continuous 모드용
  ├── KeyframeMappingEngine      blend sequential용
  └── WebSocketServer
          │ ws://localhost:8765
          ▼
[Unity]
  └── WebSocketClient
        └── AnimalController.ApplyJoints()
              └── Transform.localRotation 갱신
```

---

## 세팅 파이프라인 (한 번만 실행)

```
Step 1. Unity AnimPoseExporter 실행
        → spider.json, bone_map_spider.json, spider_poses.json 생성

Step 2. python scripts/auto_chains.py --animal spider
        → spider.json에 chains 필드 추가

Step 3. python scripts/generate_mappings.py --animal spider
        → spider_mapping.json 생성

Step 4. python mapping/lda_triggers.py --animal spider
        → spider_triggers_lda.json 생성

Step 5. Unity AnimalControllerEditor
        → "JointEntry 전체 자동 생성" 버튼 클릭
        → AnimalController Inspector 세팅 완료
```

---

## 런타임 파이프라인 (매 프레임)

```
웹캠 프레임
    │
    ▼ MediaPipe
손 랜드마크 (21점 × 양손)
    │
    ▼ compute_dof_angles()
DOF 각도 dict
  left:  {wrist_flex, wrist_dev, wrist_rot, thumb_cmc, ... pinky_dip}  20개
  right: {동일}                                                          20개
    │
    ▼ OcclusionHandler + EMA 스무딩
스무딩된 DOF
    │
    ├─────────────────────────────────────────────────────────────┐
    ▼ MappingEngine.transform_clamped()                          │
joints_d (direct 결과)                                           │
  {"l_leg": {"x":0,"y":0,"z":-32.5}, "r_leg":{"z":28.1}, ...}  │
    │                                                             │
    ▼ 3° deadzone                                                │
    │                                                             │
    ├── LDA / wrist_death 트리거 체크 ───────────────────────────┤
    │       ↓ 발동                                                │
    │   KeyframeMappingEngine.get_sequential_pose(anim, cursor)   │
    │   joints_kf = 애니메이션 키프레임 보간 결과                  │
    │       ↓                                                     │
    │   alpha = sin curve                                         │
    │   joints = (1-α)*joints_d + α*joints_kf                    │
    │                                                             │
    └─────────────── joints_d (continuous, α=0) ─────────────────┘
    │
    ▼ body joints 처리
  bone, bodyik_001, atack1 → 0 명시 전송 (또는 keyframe 값)
    │
    ▼ WebSocket 전송
{"bone":{"x":0,"y":0,"z":0}, "l_leg":{"x":0,"y":0,"z":-32.5}, ...}
    │
    ▼ Unity AnimalController.ApplyJoints()
  _targetAngles 업데이트
    │
    ▼ Update() (60fps)
  Quaternion.Lerp(current, BuildRotation(entry, target), lerpSpeed * dt)
    │
    ▼ Transform.localRotation 적용
```

---

## 관절 회전 계산 상세

### Python 측
```
DOF 각도 (wrist_flex = 15°)
    × scale_factor (2.5)
    = 관절 각도 (37.5°)
    → axis에 따라 xyz 배치: {"z": 37.5}  (axis=Z이면)
```

### Unity 측 (AnimalController.BuildRotation)
```
받은 값: {"x":0, "y":0, "z":37.5}
axisZ=true이면: az = Clamp(37.5, min, max) = 37.5
delta = Quaternion.Euler(0, 0, 37.5)
result = restRotation * delta
         = (Idle 포즈) × (추가 37.5° Z 회전)
```

---

## 상태머신 (blend 모드)

```
initial: continuous
    │
    ├─ LDA Attack1 발동 → action (Attack1)
    │       cursor: 0 → frame_count
    │       alpha: 0 → 1 → 0 (sin 전체)
    │       종료 → cooldown 45f → continuous
    │
    ├─ LDA Attack2 발동 → action (Attack2)  (동일)
    │
    └─ wrist_death 발동 → action (Death)
            cursor: 0 → frame_count
            alpha: 0 → 1 (유지)    ← sin 전반부만
            종료 → cooldown → continuous
```

---

## 현재 알려진 미해결 문제

| 문제 | 상태 | 비고 |
|------|------|------|
| bone/bodyik_001 axis (Death 뒤집기) | 미검증 | Z→X 변경 후 Unity 테스트 필요 |
| Attack1/2 LDA 실제 발동 확인 | 미테스트 | threshold 수정 후 첫 테스트 |
| 캘리브 포즈 정상 여부 | 미테스트 | restRotation 재캡처 수정 후 |

---

## 파일별 역할 요약

| 파일 | 역할 |
|------|------|
| `main.py` | 전체 파이프라인 진입점, blend 상태머신 |
| `mapping/keyframe_engine.py` | 키프레임 블렌딩 + sequential 재생 |
| `mapping/mapping_engine.py` | Direct DOF→관절 매핑 |
| `scripts/generate_mappings.py` | 최적화 매핑 파일 생성 |
| `data/animal_skeletons/spider.json` | 골격 정의 (axis, ROM) |
| `data/animal_skeletons/bone_map_spider.json` | Unity Transform 경로 |
| `data/animal_skeletons/spider_poses.json` | 애니메이션 프레임 데이터 |
| `data/mappings/spider_mapping.json` | 최적화된 손↔관절 매핑 |
| `data/mappings/spider_body_mapping.json` | 몸통 관절 수동 매핑 |
| `data/mappings/spider_triggers_lda.json` | LDA 트리거 가중치 |
| `AnimalController.cs` | Unity 관절 제어, restRotation, Lerp |
| `AnimPoseExporter.cs` | Unity 애니메이션 데이터 추출 |
| `AnimalControllerEditor.cs` | Unity Inspector 자동 설정 |
