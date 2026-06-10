// GazeCalibrator.cs
// Unity 씬 안에서 9포인트 캘리브레이션 UI 표시 + Python gaze_sender.py 와 통신.
//
// 핵심 수정:
//   1. 캘리브레이션 시작 시 강제 풀스크린 (Unity UV = 실제 화면 UV 보장)
//   2. 점 표시 후 countdownSec 초 대기 → collect 커맨드 전송 (눈 이동 여유)
//   3. 완료 후 원래 화면 모드 복원

using System.Collections;
using UnityEngine;
using Newtonsoft.Json;

public class GazeCalibrator : MonoBehaviour
{
    [Header("연결 컴포넌트")]
    [SerializeField] private GazeReceiver gazeReceiver;

    [Header("타이밍")]
    [Tooltip("점 표시 후 collect 시작까지 대기 시간 (초) — 눈이 이동할 여유")]
    [SerializeField] private float countdownSec = 1.5f;

    // ── 9포인트 UV 좌표 ───────────────────────────────────────────────────────
    private static readonly Vector2[] CalibPoints =
    {
        new Vector2(0.1f, 0.1f),   // 좌상
        new Vector2(0.5f, 0.1f),   // 중상
        new Vector2(0.9f, 0.1f),   // 우상
        new Vector2(0.1f, 0.5f),   // 좌중
        new Vector2(0.5f, 0.5f),   // 중앙
        new Vector2(0.9f, 0.5f),   // 우중
        new Vector2(0.1f, 0.9f),   // 좌하
        new Vector2(0.5f, 0.9f),   // 중하
        new Vector2(0.9f, 0.9f),   // 우하
    };

    private enum State { ConnectWait, Countdown, Collecting, TrainWait, Done }
    private State  _state       = State.ConnectWait;
    private int    _stepIdx     = 0;
    private float  _dotPhase    = 0f;
    private float  _countdown   = 0f;   // 남은 카운트다운 시간
    private string _statusText  = "Python 연결 대기 중...";

    private bool _prevFullscreen;
    private Texture2D _circleTex;

    // ─────────────────────────────────────────────────────────────────────────

    private void Start()
    {
        _circleTex = MakeCircleTex(128);

        gazeReceiver.OnServerReady   += HandleServerReady;
        gazeReceiver.OnPointDone     += HandlePointDone;
        gazeReceiver.OnCalibComplete += HandleCalibComplete;
    }

    private void OnDestroy()
    {
        if (gazeReceiver == null) return;
        gazeReceiver.OnServerReady   -= HandleServerReady;
        gazeReceiver.OnPointDone     -= HandlePointDone;
        gazeReceiver.OnCalibComplete -= HandleCalibComplete;
    }

    private void Update()
    {
        if (_state == State.Done) return;
        _dotPhase += Time.deltaTime * 4f;

        if (_state == State.Countdown)
        {
            _countdown -= Time.deltaTime;
            int sec = Mathf.CeilToInt(_countdown);
            _statusText = sec > 0
                ? $"노란 점을 응시하세요  ({_stepIdx + 1} / {CalibPoints.Length})  — {sec}초 후 수집"
                : $"수집 중...  ({_stepIdx + 1} / {CalibPoints.Length})";

            if (_countdown <= 0f)
            {
                _state = State.Collecting;
                SendCollectNow();
            }
        }
    }

    // ── 이벤트 핸들러 ──────────────────────────────────────────────────────────

    private void HandleServerReady()
    {
        // 풀스크린으로 전환 (UV = 실제 화면 UV)
        _prevFullscreen = Screen.fullScreen;
        if (!Screen.fullScreen)
        {
            Screen.SetResolution(
                Screen.currentResolution.width,
                Screen.currentResolution.height,
                FullScreenMode.FullScreenWindow);
            Debug.Log("[GazeCalibrator] 풀스크린 전환 완료");
        }

        _stepIdx = 0;
        StartCountdown();
    }

    private void HandlePointDone(int step)
    {
        _stepIdx++;
        if (_stepIdx >= CalibPoints.Length)
        {
            _state      = State.TrainWait;
            _statusText = "모델 학습 중...";
            gazeReceiver.SendCommand(JsonConvert.SerializeObject(new { cmd = "train" }));
            Debug.Log("[GazeCalibrator] train 커맨드 전송");
        }
        else
        {
            StartCountdown();
        }
    }

    private void HandleCalibComplete()
    {
        _state = State.Done;

        // 원래 화면 모드 복원
        if (!_prevFullscreen)
            Screen.fullScreen = false;

        Debug.Log("[GazeCalibrator] 완료. 시선으로 Sphere 조종 시작.");
    }

    // ── 카운트다운 시작 ────────────────────────────────────────────────────────

    private void StartCountdown()
    {
        _state     = State.Countdown;
        _countdown = countdownSec;
        Vector2 uv = CalibPoints[_stepIdx];
        _statusText = $"노란 점을 응시하세요  ({_stepIdx + 1} / {CalibPoints.Length})";
        Debug.Log($"[GazeCalibrator] Step {_stepIdx + 1}: 카운트다운 {countdownSec}s  UV=({uv.x},{uv.y})");
    }

    // ── Python 으로 collect 커맨드 전송 ───────────────────────────────────────

    private void SendCollectNow()
    {
        Vector2 uv = CalibPoints[_stepIdx];
        var cmd = new { cmd = "collect", step = _stepIdx + 1, uv_x = uv.x, uv_y = uv.y };
        gazeReceiver.SendCommand(JsonConvert.SerializeObject(cmd));
        Debug.Log($"[GazeCalibrator] collect 전송: step={_stepIdx + 1}  UV=({uv.x},{uv.y})");
    }

    // ── OnGUI: 풀스크린 오버레이 ─────────────────────────────────────────────

    private void OnGUI()
    {
        if (_state == State.Done) return;

        int sw = Screen.width;
        int sh = Screen.height;

        // 반투명 검정 배경
        GUI.color = new Color(0f, 0f, 0f, 0.82f);
        GUI.DrawTexture(new Rect(0, 0, sw, sh), Texture2D.whiteTexture);
        GUI.color = Color.white;

        // 하단 상태 텍스트
        var style = new GUIStyle(GUI.skin.label)
        {
            fontSize  = 24,
            alignment = TextAnchor.MiddleCenter,
        };
        style.normal.textColor = Color.white;
        GUI.Label(new Rect(0, sh - 70f, sw, 50f), _statusText, style);

        // 단계 표시 (우상단)
        if (_state is State.Countdown or State.Collecting)
        {
            var small = new GUIStyle(GUI.skin.label) { fontSize = 18 };
            small.normal.textColor = new Color(1f, 1f, 1f, 0.55f);
            GUI.Label(new Rect(sw - 170f, 18f, 150f, 30f),
                      $"{_stepIdx + 1}  /  {CalibPoints.Length}", small);
        }

        // 캘리브레이션 점
        if ((_state is State.Countdown or State.Collecting) && _stepIdx < CalibPoints.Length)
        {
            Vector2 uv = CalibPoints[_stepIdx];
            float   cx = uv.x * sw;
            float   cy = uv.y * sh;

            // 카운트다운 중: 점이 작고 흰색 → 수집 중: 노란색 맥동
            bool isCollecting = (_state == State.Collecting);

            // 외곽 링
            float outerR = 42f;
            GUI.color = new Color(1f, 1f, 1f, isCollecting ? 0.2f : 0.4f);
            GUI.DrawTexture(new Rect(cx - outerR, cy - outerR, outerR * 2, outerR * 2), _circleTex);

            // 내부 점
            float pulse  = isCollecting ? (0.75f + 0.25f * Mathf.Sin(_dotPhase)) : 1f;
            float innerR = 20f * pulse;
            GUI.color = isCollecting
                ? new Color(1f, 0.92f, 0.1f, 1f)     // 노란색 (수집 중)
                : new Color(1f, 1f, 1f, 0.85f);       // 흰색 (대기 중)
            GUI.DrawTexture(new Rect(cx - innerR, cy - innerR, innerR * 2, innerR * 2), _circleTex);

            // 카운트다운 진행 원호 표시 (남은 시간 = 반지름)
            if (_state == State.Countdown && countdownSec > 0f)
            {
                float progress = 1f - (_countdown / countdownSec);   // 0→1
                float arcR = 55f;
                GUI.color = new Color(0.4f, 0.8f, 1f, 0.6f);
                // 간단 근사: progress 만큼 채워지는 원을 픽셀로 그리기 어려우므로
                // 아래쪽에 progress 바로 대신 표시
                float barW  = 200f * progress;
                float barH  = 6f;
                GUI.DrawTexture(new Rect(cx - 100f, cy + 36f, barW, barH), Texture2D.whiteTexture);
            }

            GUI.color = Color.white;
        }
    }

    // ── 원형 텍스처 ───────────────────────────────────────────────────────────

    private static Texture2D MakeCircleTex(int size)
    {
        var   tex = new Texture2D(size, size, TextureFormat.ARGB32, false);
        float r   = size / 2f;
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = x - r + 0.5f, dy = y - r + 0.5f;
            float a  = Mathf.Clamp01((r - Mathf.Sqrt(dx * dx + dy * dy)) * 2.5f);
            tex.SetPixel(x, y, new Color(1f, 1f, 1f, a));
        }
        tex.Apply();
        return tex;
    }
}
