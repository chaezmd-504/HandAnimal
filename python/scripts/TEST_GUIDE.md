# HandAnimal 테스트 가이드

Unity Play 모드를 켜 놓은 상태에서 아래 명령어로 Python 테스트 가능.  
카메라/MediaPipe 없이 합성 DOF로 파이프라인 전체를 테스트한다.

---

## Spider 테스트

```bash
# 기본 보행
python scripts/mock_sender.py --animal spider --mode walk

# 방향 제어 (오른손 wrist_dev 고정)
python scripts/mock_sender.py --animal spider --steer 15   # 우회전
python scripts/mock_sender.py --animal spider --steer -15  # 좌회전

# ROM 전체 확인 (모든 DOF 사인파)
python scripts/mock_sender.py --animal spider --mode wave

# 트리거 테스트 (3초 후 발동)
python scripts/mock_sender.py --animal spider --trigger Attack1 --trigger-at 3
python scripts/mock_sender.py --animal spider --trigger Attack2 --trigger-at 3
```

| 모드 | 설명 |
|------|------|
| `idle` | 중립 포즈 고정 (캘리브 기준 확인용) |
| `walk` | 4손가락 동시 쥐었다 펴기 (0.8Hz) |
| `wave` | 전체 DOF 사인파 순환 (ROM 확인) |
| `wrist` | wrist_flex/dev/rot 위주 사인파 |

---

## Horse 테스트

```bash
# 기본 보행 (Trot 패턴 + 머리 bobbing)
python scripts/test_horse.py --mode walk --speed 1.0

# 다리만 (Trot 대각선 패턴)
python scripts/test_horse.py --mode legs --speed 1.0

# 머리/척추만 (오른손 wrist 반응 확인)
python scripts/test_horse.py --mode head --speed 1.0

# 빠른 갤럽
python scripts/test_horse.py --mode run --speed 1.0

# 방향 제어 (steer 사용 시 직진 오버라이드 해제됨)
python scripts/test_horse.py --mode walk --steer 10

# 트리거 테스트 (4초 후 발동)
python scripts/test_horse.py --trigger Horse_001_eat --trigger-at 4
python scripts/test_horse.py --trigger Horse_001_run --trigger-at 4
python scripts/test_horse.py --trigger Horse_001_idle --trigger-at 4
```

| 모드 | 왼손(다리) | 오른손(머리/척추) |
|------|-----------|-----------------|
| `idle` | 중립 고정 | 중립 고정 |
| `legs` | Trot 패턴 | 중립 고정 |
| `head` | 중립 고정 | wrist_flex/rot 사인파 |
| `walk` | Trot 패턴 | 머리 bobbing |
| `run` | 빠른 갤럽 (2.2Hz) | 빠른 머리 bobbing |

### Trot 패턴 원리
```
앞왼(검지) + 뒷오(새끼)  ← phase 0      동시에 움직임
앞오(중지) + 뒷왼(약지)  ← phase π     반대 위상
```

---

## 실제 영상으로 테스트

```bash
# 저장된 영상 파일로 실시간 카메라 대체
python -u main.py --animal spider --mapping blend --locomotion --head-dir --threshold 65 \
  --video "C:\Users\chaez\Downloads\KakaoTalk_20260608_232650369.mp4"
```

---

## 실제 실행 명령 (카메라)

```bash
# Spider
python -u main.py --animal spider --mapping blend --locomotion --head-dir --threshold 65

# Horse (Unity 씬 세팅 완료 후)
python -u main.py --animal horse --mapping direct --locomotion --head-dir --threshold 65
```

---

## Unity Inspector 권장 설정

### AnimalController (Horse)
| 항목 | 값 |
|------|-----|
| Walk Anim Name | `Horse_001_walk` |
| Walk Hand Blend | `1.0` |
| Lerp Speed | `40` |

### AnimalLocomotion (Horse)
| 항목 | 값 |
|------|-----|
| Normal Speed | `5~8` |
| Use Discrete Speed | `true` |

### AnimalController (Spider)
| 항목 | 값 |
|------|-----|
| Walk Anim Name | `Walk` |
| Walk Hand Blend | `0` (애니메이션 전담) |
| Lerp Speed | `12` |

---

## 스무딩 파라미터 (main.py)

```python
# DOF 입력 EMA (1차)
_EMA_ALPHA = 0.7              # 기본 (손가락 MCP 등)
_EMA_ALPHA_OVERRIDES = {
    "wrist_flex": 0.25,       # 노이즈 심함
    "wrist_rot":  0.20,       # 노이즈 심함
    "thumb_abd":  0.35,
}

# 관절 출력 EMA (2차)
_JOINTS_EMA_ALPHA = 0.35      # 낮을수록 부드러움, 높을수록 반응 빠름
```
