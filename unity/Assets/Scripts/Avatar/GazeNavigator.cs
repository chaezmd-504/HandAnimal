// GazeNavigator.cs
// gaze_sender.py 에서 전송된 시선 UV(gaze_x, gaze_y)를 방향 기반 이동으로 변환.
//
// ── 동작 원리 ────────────────────────────────────────────────────────────────
//   gaze_x (0=왼쪽, 0.5=정면, 1=오른쪽) → targetObject 좌우 회전
//   gaze_y : 현재 미사용 (확장 가능: 속도 조절 등)
//
//   FollowCamera가 targetObject 뒤에서 따라오므로
//   "화면 중앙 ≈ targetObject 전방" 관계가 항상 유지됨.
//   → gaze 방향 = 이동 방향이 자연스럽게 일치.
//
// ── 사용 방법 ────────────────────────────────────────────────────────────────
//   HandAvatar > Setup Gaze Test Scene 메뉴 실행 시 자동 구성됨.
//   수동 구성 시:
//     1. GazeSystem GameObject에 GazeReceiver + GazeNavigator 부착
//     2. GazeNavigator → Target Object: 움직일 Sphere
//     3. GazeReceiver → Gaze Navigator: 이 컴포넌트 연결

using UnityEngine;

public class GazeNavigator : MonoBehaviour
{
    [Header("동물 연동 (선택)")]
    [Tooltip("연결 시 Sphere 이동 대신 동물 방향 제어. 비워두면 Target Object 이동.")]
    [SerializeField] private AnimalLocomotion animalLocomotion;

    [Header("이동 대상 (독립 테스트용)")]
    [Tooltip("animalLocomotion 미연결 시 이 오브젝트를 직접 조종 (Sphere 등)")]
    [SerializeField] private Transform targetObject;

    [Tooltip("전진 속도 (m/s)")]
    [SerializeField] private float moveSpeed = 3f;

    [Tooltip("최대 회전 속도 (deg/s)")]
    [SerializeField] private float turnSpeed = 90f;

    [Header("시선 제어")]
    [Tooltip("좌우 불감대 — 중앙 ±deadZone 이내는 직진 (0~0.5)")]
    [SerializeField] [Range(0f, 0.4f)] private float deadZone = 0.1f;

    [Tooltip("EMA 스무딩 계수 — 낮을수록 느리게 반응")]
    [SerializeField] [Range(0.05f, 1f)] private float gazeEma = 0.2f;

    [Header("미감지 처리")]
    [Tooltip("이 프레임 수 이상 gaze 없으면 이동 정지")]
    [SerializeField] private int missStopFrames = 10;

    [Header("디버그")]
    [SerializeField] private bool showLog = false;

    // ── 내부 상태 ─────────────────────────────────────────────────────────────
    private float _smoothX = 0.5f;
    private float _smoothY = 0.5f;
    private bool  _hasGaze = false;
    private int   _missCnt = 0;

    // ─────────────────────────────────────────────────────────────────────────
    // 외부 API — GazeReceiver 에서 매 메시지마다 호출
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>AnimalSwitcher가 동물 전환 시 호출해 연동 대상을 교체한다.</summary>
    public void SetAnimalLocomotion(AnimalLocomotion loco)
    {
        animalLocomotion = loco;
    }

    public void OnGazeUpdate(float gazeX, float gazeY)
    {
        _missCnt = 0;
        _hasGaze = true;
        _smoothX = gazeEma * gazeX + (1f - gazeEma) * _smoothX;
        _smoothY = gazeEma * gazeY + (1f - gazeEma) * _smoothY;
    }

    public void OnGazeLost()
    {
        _missCnt++;
        if (_missCnt >= missStopFrames)
        {
            _hasGaze = false;
            animalLocomotion?.OnGazeLost();
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Update — 방향 전환 + 전진
    // ─────────────────────────────────────────────────────────────────────────

    private void Update()
    {
        if (!_hasGaze) return;
        if (animalLocomotion == null && targetObject == null) return;

        // ── 1. 좌우 회전: gaze_x → yaw delta ────────────────────────────────
        //   xOffset: -0.5(좌 끝) ~ 0(중앙) ~ +0.5(우 끝)
        float xOffset = _smoothX - 0.5f;
        float normalizedTurn = 0f;

        if (Mathf.Abs(xOffset) > deadZone)
        {
            // 불감대 제거 후 -1 ~ +1 로 정규화
            normalizedTurn = Mathf.Sign(xOffset)
                           * (Mathf.Abs(xOffset) - deadZone)
                           / (0.5f - deadZone);
            normalizedTurn = Mathf.Clamp(normalizedTurn, -1f, 1f);
        }

        float yawDelta = normalizedTurn * turnSpeed * Time.deltaTime;

        if (animalLocomotion != null)
        {
            // ── 동물 연동 모드: yaw를 AnimalLocomotion 에 전달 ────────────────
            animalLocomotion.SetGazeYaw(normalizedTurn);
        }
        else if (targetObject != null)
        {
            // ── 독립 테스트 모드: Sphere 직접 조종 ───────────────────────────
            targetObject.Rotate(Vector3.up, yawDelta, Space.World);
            targetObject.position += targetObject.forward * moveSpeed * Time.deltaTime;
        }

        if (showLog)
            Debug.Log($"[GazeNavigator] gaze=({_smoothX:F2},{_smoothY:F2})"
                    + $"  turn={yawDelta:F1}°  norm={normalizedTurn:F2}");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Gizmos — 전방 방향 시각화
    // ─────────────────────────────────────────────────────────────────────────

    private void OnDrawGizmosSelected()
    {
        if (targetObject == null) return;
        Gizmos.color = _hasGaze ? new Color(0.2f, 1f, 0.3f) : Color.red;
        Gizmos.DrawRay(targetObject.position, targetObject.forward * 4f);
        Gizmos.DrawWireSphere(targetObject.position + targetObject.forward * 4f, 0.2f);
    }
}
