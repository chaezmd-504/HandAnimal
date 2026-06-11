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
    // ── 방향 제어 소스 ────────────────────────────────────────────────────────
    public enum YawSource
    {
        HeadDir,   // 기존 Python head-dir (--head-dir 플래그)
        Gaze,      // 시선 제어 (gaze_sender.py)
        Auto,      // gaze 연결되면 자동 전환, 미연결 시 head-dir
    }

    [Header("방향 제어 소스")]
    [Tooltip("HeadDir = 기존 head-dir  |  Gaze = 시선  |  Auto = gaze 연결 시 자동 전환")]
    [SerializeField] private YawSource yawSource = YawSource.Auto;
    [Tooltip("Gaze/Auto 모드 최대 회전 속도 (deg/s)")]
    [SerializeField] private float gazeMaxTurnSpeed = 90f;

    [Header("이동 배율")]
    [Tooltip("yaw_delta 에 곱해지는 회전 배율 (HeadDir 모드)")]
    [SerializeField] private float rotationMultiplier = 1.0f;

    [Header("이산 속도 (Discrete Speed)")]
    [Tooltip("이산 속도 모드 활성화 — 속도를 0/Normal/Fast 3단계로 양자화")]
    [SerializeField] private bool useDiscreteSpeed = true;
    [Tooltip("Python speed 이 이 값 미만이면 정지 (idle)")]
    [SerializeField] private float idleThreshold  = 0.05f;
    [Tooltip("Normal 이동 속도 (Unity units/s)")]
    [SerializeField] private float normalSpeed    = 20f;

    [Header("감쇠 설정")]
    [Tooltip("손 미감지 시 속도 감쇠 계수 (0=즉시 멈춤, 1=감쇠 없음)")]
    [Range(0f, 1f)]
    [SerializeField] private float speedDecay = 0f;

    [Header("디버그")]
    [SerializeField] private bool showDebugLog = false;
    [SerializeField] private bool showHUD      = true;

    // ── 내부 상태 ─────────────────────────────────────────────────────────────
    private float _speed;
    private float _yawDelta;    // head-dir yaw (deg/frame, Python에서)
    private bool  _valid;
    private float _moveSpeed;   // OnGUI 표시용

    // Gaze 상태
    private float _gazeNorm     = 0f;   // -1(좌)~+1(우), GazeNavigator에서 설정
    private bool  _gazeActive   = false;
    private int   _gazeMissCnt  = 0;
    private const int GazeMissReset = 10;

    /// <summary>실제로 이동 중인지 (AnimalController Walk 판단용)</summary>
    public bool IsMoving => _moveSpeed > 0.001f;

    // ── Gaze 외부 API (GazeNavigator 에서 호출) ───────────────────────────────

    /// <summary>
    /// GazeNavigator 에서 매 프레임 호출. normalizedTurn: -1(좌)~0(직진)~+1(우).
    /// </summary>
    public void SetGazeYaw(float normalizedTurn)
    {
        _gazeNorm    = normalizedTurn;
        _gazeActive  = true;
        _gazeMissCnt = 0;
    }

    /// <summary>gaze 미감지 시 GazeNavigator 에서 호출.</summary>
    public void OnGazeLost()
    {
        _gazeMissCnt++;
        if (_gazeMissCnt >= GazeMissReset)
        {
            _gazeActive = false;
            _gazeNorm   = 0f;
        }
    }

    // ──────────────────────────────────────────────────────────
    // 외부 API — WebSocketClient 에서 호출
    // ──────────────────────────────────────────────────────────

    /// <summary>
    /// Python LocomotionMapper 결과를 적용한다. 매 WebSocket 프레임마다 호출.
    /// </summary>
    public void ApplyLocomotion(float speed, float yawDelta, bool valid)
    {
        // valid=false(손 미감지)이면 Python 값 무시 — Update()에서 decay
        if (valid)
            _speed = speed;
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
            // 손 미감지: 속도 감쇠 (yaw 는 그대로 유지 — head-dir 모드에서 계속 회전 필요)
            _speed *= speedDecay;
        }

        // ── 방향 소스 결정 ────────────────────────────────────────────────────
        bool useGaze = yawSource == YawSource.Gaze
                    || (yawSource == YawSource.Auto && _gazeActive);

        float activeYaw = useGaze
            ? _gazeNorm * gazeMaxTurnSpeed * Time.deltaTime   // deg this frame
            : _yawDelta;                                       // Python head-dir (deg/frame)

        // 회전 (Y 축)
        if (Mathf.Abs(activeYaw) > 0.01f)
        {
            transform.Rotate(
                Vector3.up,
                activeYaw * rotationMultiplier,
                Space.World
            );
        }

        // 전진 이동
        float moveSpeed = 0f;
        if (useDiscreteSpeed)
        {
            if (_speed >= idleThreshold) moveSpeed = normalSpeed;
        }
        else
        {
            moveSpeed = _speed;
        }
        _moveSpeed = moveSpeed;

        if (moveSpeed > 0f)
        {
            transform.Translate(
                Vector3.forward * moveSpeed * Time.deltaTime,
                Space.Self
            );
        }

        if (showDebugLog && (_speed > 0.001f || Mathf.Abs(_yawDelta) > 0.01f))
        {
            Debug.Log($"[AnimalLocomotion] rawSpeed={_speed:F3}  moveSpeed={moveSpeed:F1}  yaw={_yawDelta:+0.0;-0.0}°  valid={_valid}");
        }
    }

    // ──────────────────────────────────────────────────────────
    // HUD
    // ──────────────────────────────────────────────────────────

    private void OnGUI()
    {
        if (!showHUD) return;

        // 속도 상태
        string speedLabel;
        Color  speedColor;
        if (_moveSpeed >= normalSpeed - 0.01f)
        {
            speedLabel = "NORMAL SPEED";
            speedColor = new Color(0.2f, 1f, 0.2f); // 초록
        }
        else
        {
            speedLabel = "STOP";
            speedColor = new Color(0.6f, 0.6f, 0.6f); // 회색
        }

        // 방향 소스 표시
        bool   hudUseGaze = yawSource == YawSource.Gaze || (yawSource == YawSource.Auto && _gazeActive);
        float  hudYaw     = hudUseGaze ? _gazeNorm * gazeMaxTurnSpeed * Time.deltaTime : _yawDelta;
        string srcLabel   = hudUseGaze ? "[GAZE]" : "[HEAD]";

        string dirLabel = "";
        Color  dirColor = Color.white;
        if (hudYaw > 0.1f)
        {
            dirLabel = $"{srcLabel} TURNING RIGHT  ({hudYaw:+0.00})";
            dirColor = new Color(0.4f, 0.8f, 1f);
        }
        else if (hudYaw < -0.1f)
        {
            dirLabel = $"{srcLabel} TURNING LEFT   ({hudYaw:+0.00})";
            dirColor = new Color(1f, 0.8f, 0.2f);
        }

        var style = new GUIStyle(GUI.skin.label)
        {
            fontSize  = 28,
            fontStyle = FontStyle.Bold,
            alignment = TextAnchor.UpperLeft,
        };

        int x = 20, y = 20;

        // 속도
        style.normal.textColor = Color.black;
        GUI.Label(new Rect(x + 2, y + 2, 400, 50), speedLabel, style);
        style.normal.textColor = speedColor;
        GUI.Label(new Rect(x, y, 400, 50), speedLabel, style);

        // 방향 (있을 때만)
        if (dirLabel != "")
        {
            y += 42;
            style.normal.textColor = Color.black;
            GUI.Label(new Rect(x + 2, y + 2, 400, 50), dirLabel, style);
            style.normal.textColor = dirColor;
            GUI.Label(new Rect(x, y, 400, 50), dirLabel, style);
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
