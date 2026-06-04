// AnimalLocomotion.cs
// Python LocomotionMapper 에서 수신한 speed / yaw_delta 로 동물을 실제로 이동시킨다.
//
// 사용 방법:
//   1. 동물 GameObject 에 이 컴포넌트를 추가한다.
//   2. WebSocketClient Inspector 의 "Animal Locomotion" 슬롯에 연결한다.
//   3. Python main.py 를 --locomotion 플래그로 실행한다.
//
// 이동 원리:
//   - yaw_delta (°/frame) → Y 축 회전 누적 (방향 전환)
//   - speed               → forward 이동 (Transform.Translate)
//   - 손 미감지 시 속도는 자연 감쇠, 회전은 즉시 정지

using UnityEngine;

public class AnimalLocomotion : MonoBehaviour
{
    [Header("이동 배율")]
    [Tooltip("Python speed 값에 곱해지는 이동 배율 (기본 1.0)")]
    [SerializeField] private float speedMultiplier = 1.0f;

    [Tooltip("yaw_delta 에 곱해지는 회전 배율 (기본 1.0)")]
    [SerializeField] private float rotationMultiplier = 1.0f;

    [Header("감쇠 설정")]
    [Tooltip("손 미감지 시 속도 감쇠 계수 (0=즉시 멈춤, 1=감쇠 없음)")]
    [Range(0f, 1f)]
    [SerializeField] private float speedDecay = 0.85f;

    [Header("디버그")]
    [SerializeField] private bool showDebugLog = false;

    // 현재 상태 (외부에서 ApplyLocomotion 으로 갱신)
    private float _speed;
    private float _yawDelta;
    private bool  _valid;

    // ──────────────────────────────────────────────────────────
    // 외부 API — WebSocketClient 에서 호출
    // ──────────────────────────────────────────────────────────

    /// <summary>
    /// Python LocomotionMapper 결과를 적용한다. 매 WebSocket 프레임마다 호출.
    /// </summary>
    public void ApplyLocomotion(float speed, float yawDelta, bool valid)
    {
        _speed    = speed;
        _yawDelta = yawDelta;
        _valid    = valid;
    }

    // ──────────────────────────────────────────────────────────
    // Unity 업데이트
    // ──────────────────────────────────────────────────────────

    private void Update()
    {
        if (!_valid)
        {
            // 손 미감지: 속도 감쇠
            _speed    *= speedDecay;
            _yawDelta  = 0f;
        }

        // 회전 (Y 축)
        if (Mathf.Abs(_yawDelta) > 0.01f)
        {
            transform.Rotate(
                Vector3.up,
                _yawDelta * rotationMultiplier,
                Space.World
            );
        }

        // 전진 이동
        if (_speed > 0.001f)
        {
            transform.Translate(
                Vector3.forward * _speed * speedMultiplier * Time.deltaTime,
                Space.Self
            );
        }

        if (showDebugLog && (_speed > 0.001f || Mathf.Abs(_yawDelta) > 0.01f))
        {
            Debug.Log($"[AnimalLocomotion] speed={_speed:F3}  yaw={_yawDelta:+0.0;-0.0}°  valid={_valid}");
        }
    }

    // ──────────────────────────────────────────────────────────
    // 상태 초기화 (동물 전환 시 AnimalSwitcher 가 호출)
    // ──────────────────────────────────────────────────────────

    public void ResetLocomotion()
    {
        _speed    = 0f;
        _yawDelta = 0f;
        _valid    = false;
    }
}
