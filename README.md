# HandAnimal

손(MediaPipe Hand Tracking) + 시선으로 동물 아바타를 실시간 제어하는 Unity + Python 프로젝트.

---

## 빠른 시작

### 환경 요구사항

| | 버전 |
|--|--|
| Python | 3.10 (conda `capstone_env`) |
| Unity | 2022.3 LTS 이상 |
| 웹캠 | 필수 |

### Python 패키지

```bash
conda activate capstone_env
pip install mediapipe opencv-python websockets numpy
pip install eyetrax      # gaze 모드 사용 시
```

---

## 실행 방법

### 1. Unity 씬 열기
`unity/Assets/SimpleNaturePack_Demo.unity`

### 2. Python 실행 (Unity Play 전에 먼저 실행)

**Spider**
```bash
conda activate capstone_env
python -u main.py --animal spider --mapping blend --locomotion --head-dir
```

**Horse**
```bash
python -u main.py --animal horse --mapping blend --locomotion --head-dir
```

**Gaze 방향 제어 (eyetrax 필요)**
```bash
python -u main.py --animal horse --mapping blend --locomotion --gaze
```

**노트북 저사양 모드** (Unity + Python 같은 PC에서 렉 감소)
```bash
python -u main.py --animal spider --mapping blend --locomotion --head-dir --fast
```

### 3. Unity Play → 손 캘리브레이션
화면 지시에 따라 양손을 캘리브레이션 포즈로 유지 → 완료되면 제어 시작.

---

## Python 주요 플래그

| 플래그 | 설명 |
|--|--|
| `--animal spider\|horse` | 동물 선택 |
| `--mapping blend` | 권장 매핑 방식 |
| `--locomotion` | 이동 모듈 활성화 (손 속도 → 이동) |
| `--head-dir` | 머리 방향으로 이동 방향 제어 |
| `--gaze` | 시선 추적으로 이동 방향 제어 (eyetrax) |
| `--fast` | 저사양 모드: 480×270, 1프레임 스킵, 창 비활성 |
| `--cam-width N` | 웹캠 가로 해상도 직접 지정 |
| `--cam-height N` | 웹캠 세로 해상도 직접 지정 |
| `--skip N` | N프레임 스킵 (0=스킵 없음) |
| `--no-window` | OpenCV 미리보기 창 비활성 |

---

## 손 조작법

### Spider
| 손 | 손가락 | 제어 |
|--|--|--|
| 왼손 | 검지~새끼 | 왼쪽 4다리 |
| 오른손 | 검지~새끼 | 오른쪽 4다리 |
| 머리/gaze | | 방향 전환 |

### Horse
| 손 | 제어 |
|--|--|
| 왼손 4손가락 | 4다리 (검지→앞왼, 중지→앞오, 약지→뒷왼, 새끼→뒷오) |
| 오른손 손목 굽힘 | 머리(scull) + 목(spine_008) |
| 오른손 손가락 | 척추 |
| 오른손 새끼 굽힘 | eat 트리거 |
| 머리/gaze | 방향 전환 |

---

## Unity Inspector 주요 설정

### AnimalController
| 필드 | Spider | Horse |
|--|--|--|
| `walkAnimName` | `Walk` | `Horse_001_walk` |
| `restPoseClip` | `Idle.anim` | idle 클립 지정 권장 |
| `walkHandBlend` | 0.48 | 0.57 |
| `triggerHandBlend` | 0.3 | 0.31 |

> **`restPoseClip`**: 씬 시작 시 이 클립 frame 0을 restRotation 기준으로 캡처.
> Spider는 반드시 `Idle.anim` 지정 (bind pose가 다리 뻗힌 자세라 필수).
> Horse는 적절한 idle/standing 클립 지정 시 자연스러운 기립 자세 유지.

### AnimalLocomotion
| 필드 | 설명 |
|--|--|
| `Yaw Source` | `HeadDir` / `Gaze` / `Auto` |
| `Normal Speed` | 이동 속도 (Unity units/s) |
| `Use Discrete Speed` | On: 0/Normal 2단계 |

---

## 방향 제어 모드

| 모드 | Python 플래그 | Unity YawSource |
|--|--|--|
| Head-dir | `--head-dir` | HeadDir |
| Gaze (통합) | `--gaze` | Gaze 또는 Auto |
| Gaze (독립 프로세스) | *(없음)* | Gaze |

**Gaze 독립 실행 — 저사양 환경 권장** (gaze가 별도 프로세스로 CPU 분리)
```bash
# 터미널 1
python -u main.py --animal horse --mapping blend --locomotion --fast

# 터미널 2
python -u scripts/gaze_sender.py
```

---

## 파일 구조

```
HandAnimal/
├── python/
│   ├── main.py                        # 메인 파이프라인
│   ├── mapping/
│   │   ├── mapping_engine.py          # Direct 매핑 엔진
│   │   ├── keyframe_engine.py         # Blend 매핑 엔진
│   │   └── locomotion_mapper.py       # 로코모션 계산
│   ├── tracking/
│   │   ├── hand_tracker.py            # MediaPipe 손 추적
│   │   └── face_tracker.py           # 얼굴 yaw 추적
│   ├── communication/
│   │   └── websocket_server.py        # Unity 통신
│   ├── data/
│   │   ├── mappings/
│   │   │   ├── spider_mapping.json    # Spider 관절 매핑 + reference_pose
│   │   │   ├── horse_mapping.json     # Horse 관절 매핑 + reference_pose
│   │   │   └── locomotion_config.json # 로코모션 파라미터
│   │   └── animal_skeletons/
│   │       ├── spider.json            # Spider 스켈레톤/ROM
│   │       └── horse.json            # Horse 스켈레톤/ROM
│   └── scripts/
│       └── gaze_sender.py             # 독립 Gaze 프로세스
└── unity/
    └── Assets/
        ├── Scripts/
        │   ├── Avatar/
        │   │   ├── AnimalController.cs  # 관절 제어 핵심
        │   │   ├── AnimalLocomotion.cs  # 이동/방향 제어
        │   │   ├── AnimalSwitcher.cs    # 동물 전환
        │   │   ├── GazeNavigator.cs     # Gaze 방향 제어
        │   │   └── GazeCalibrator.cs    # Gaze 9포인트 캘리브
        │   └── Communication/
        │       └── WebSocketClient.cs   # Python 수신
        └── SimpleNaturePack_Demo.unity  # 메인 씬
```

---

## 트러블슈팅

| 증상 | 해결 |
|--|--|
| Spider 다리가 뻗어있음 | AnimalController → `Rest Pose Clip` = `Idle.anim` |
| Horse 머리가 숙여짐 | AnimalController → `Rest Pose Clip`에 idle 클립 지정 |
| Unity에 데이터 안 들어옴 | Python 먼저 실행 후 Unity Play / 포트 8765 확인 |
| 렉/프레임 드랍 | `--fast` 플래그 추가 |
| 캘리브레이션 실패 | 양손이 카메라에 잘 보이는지, 조명 확인 |
