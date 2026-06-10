// GazeReceiver.cs
// gaze_sender.py 와 양방향 통신하는 WebSocket 클라이언트.
//
// Python → Unity 메시지:
//   {"type": "ready"}                          → OnServerReady 이벤트
//   {"type": "point_done", "step": N}          → OnPointDone 이벤트
//   {"type": "calib_complete"}                 → OnCalibComplete 이벤트
//   {"gaze_x": X, "gaze_y": Y, "valid": B}    → GazeNavigator 전달
//
// Unity → Python 메시지 (SendCommand):
//   {"cmd": "collect", "step": N, "uv_x": X, "uv_y": Y}
//   {"cmd": "train"}

using System;
using System.Collections;
using Newtonsoft.Json.Linq;
using UnityEngine;
using WebSocketSharp;

[Serializable]
public class GazeData
{
    public float gaze_x;
    public float gaze_y;
    public bool  valid;
}

public class GazeReceiver : MonoBehaviour
{
    [Header("서버 설정")]
    [SerializeField] private string serverUrl  = "ws://localhost:8766";
    [SerializeField] private int    maxRetries  = 10;
    [SerializeField] private float  retryDelay  = 2f;

    [Header("연결 컴포넌트")]
    [SerializeField] private GazeNavigator gazeNavigator;

    // ── 이벤트 (GazeCalibrator 에서 구독) ─────────────────────────────────────
    public event Action       OnServerReady;
    public event Action<int>  OnPointDone;
    public event Action       OnCalibComplete;

    private WebSocket _ws;
    private int  _retryCount;
    private bool _isConnecting;

    private void Start()  => Connect();
    private void OnDestroy()         => _ws?.Close();
    private void OnApplicationQuit() => _ws?.Close();

    // ── Python 으로 커맨드 전송 ────────────────────────────────────────────────
    public void SendCommand(string json)
    {
        if (_ws != null && _ws.ReadyState == WebSocketState.Open)
            _ws.Send(json);
        else
            Debug.LogWarning("[GazeReceiver] 전송 실패: WebSocket 미연결");
    }

    // ── WebSocket 연결 ─────────────────────────────────────────────────────────

    private void Connect()
    {
        if (_isConnecting) return;
        _isConnecting = true;

        _ws = new WebSocket(serverUrl);

        _ws.OnOpen += (s, e) =>
        {
            _retryCount   = 0;
            _isConnecting = false;
            MainThreadDispatcher.Enqueue(() =>
                Debug.Log($"[GazeReceiver] 연결 성공: {serverUrl}"));
        };

        _ws.OnMessage += (s, e) => OnMessage(e.Data);

        _ws.OnError += (s, e) =>
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogWarning($"[GazeReceiver] 오류: {e.Message}"));

        _ws.OnClose += (s, e) =>
        {
            _isConnecting = false;
            MainThreadDispatcher.Enqueue(() =>
            {
                Debug.Log($"[GazeReceiver] 연결 해제 ({e.Code})");
                if (_retryCount < maxRetries)
                    StartCoroutine(RetryConnect());
            });
        };

        _ws.ConnectAsync();
    }

    private IEnumerator RetryConnect()
    {
        _retryCount++;
        Debug.Log($"[GazeReceiver] 재연결 시도 {_retryCount}/{maxRetries} ({retryDelay}s 후)");
        yield return new WaitForSeconds(retryDelay);
        Connect();
    }

    // ── 메시지 라우팅 ──────────────────────────────────────────────────────────

    private void OnMessage(string json)
    {
        try
        {
            var obj  = JObject.Parse(json);
            string type = obj["type"]?.ToString();

            if (type == "ready")
            {
                MainThreadDispatcher.Enqueue(() =>
                {
                    Debug.Log("[GazeReceiver] Python 준비 완료 → 캘리브레이션 시작");
                    OnServerReady?.Invoke();
                });
            }
            else if (type == "point_done")
            {
                int step = obj["step"]?.Value<int>() ?? 0;
                MainThreadDispatcher.Enqueue(() => OnPointDone?.Invoke(step));
            }
            else if (type == "calib_complete")
            {
                MainThreadDispatcher.Enqueue(() =>
                {
                    Debug.Log("[GazeReceiver] 캘리브레이션 완료");
                    OnCalibComplete?.Invoke();
                });
            }
            else
            {
                // gaze 데이터
                float gx    = obj["gaze_x"]?.Value<float>() ?? 0.5f;
                float gy    = obj["gaze_y"]?.Value<float>() ?? 0.5f;
                bool  valid = obj["valid"]?.Value<bool>()   ?? false;
                var   data  = new GazeData { gaze_x = gx, gaze_y = gy, valid = valid };
                MainThreadDispatcher.Enqueue(() => HandleGazeData(data));
            }
        }
        catch (Exception ex)
        {
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogWarning($"[GazeReceiver] JSON 파싱 오류: {ex.Message}"));
        }
    }

    private void HandleGazeData(GazeData data)
    {
        if (gazeNavigator == null) return;
        if (data.valid)
            gazeNavigator.OnGazeUpdate(data.gaze_x, data.gaze_y);
        else
            gazeNavigator.OnGazeLost();
    }
}
