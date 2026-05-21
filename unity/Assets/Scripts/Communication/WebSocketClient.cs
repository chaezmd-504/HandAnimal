// WebSocketClient.cs
// Python WebSocket 서버(ws://localhost:8765)에서 관절 데이터를 수신한다.
// 수신한 JSON을 파싱해 AnimalController와 AnimalSwitcher에 전달한다.
//
// 의존성:
//   - WebSocketSharp.dll  → Assets/Plugins/ 에 배치
//   - Newtonsoft.Json     → Package Manager에서 설치
//   - MainThreadDispatcher (같은 씬의 GameObject에 부착)

using System;
using System.Collections;
using Newtonsoft.Json;
using UnityEngine;
using WebSocketSharp;

[Serializable]
public class JointRotation
{
    public float x;
    public float y;
    public float z;
}

[Serializable]
public class FrameData
{
    public string type;
    public string animal;
    public System.Collections.Generic.Dictionary<string, JointRotation> joints;
    public bool hand_detected;
    public string gesture;
}

public class WebSocketClient : MonoBehaviour
{
    [Header("서버 설정")]
    [SerializeField] private string serverUrl = "ws://localhost:8765";
    [SerializeField] private int maxRetries    = 5;
    [SerializeField] private float retryDelay  = 3f;

    [Header("연결 컴포넌트")]
    [SerializeField] private AnimalSwitcher   animalSwitcher;
    [Tooltip("AnimalSwitcher가 없을 때만 사용하는 폴백 컨트롤러")]
    [SerializeField] private AnimalController animalController;

    private WebSocket _ws;
    private int _retryCount;
    private bool _isConnecting;

    // ──────────────────────────────────────────────────────────
    // Unity 생명주기
    // ──────────────────────────────────────────────────────────

    private void Start()
    {
        Connect();
    }

    private void OnDestroy()
    {
        _ws?.Close();
    }

    private void OnApplicationQuit()
    {
        _ws?.Close();
    }

    // ──────────────────────────────────────────────────────────
    // WebSocket 연결
    // ──────────────────────────────────────────────────────────

    private void Connect()
    {
        if (_isConnecting) return;
        _isConnecting = true;

        _ws = new WebSocket(serverUrl);

        _ws.OnOpen += (sender, e) =>
        {
            _retryCount   = 0;
            _isConnecting = false;
            MainThreadDispatcher.Enqueue(() =>
                Debug.Log($"[WebSocketClient] Python 서버 연결 성공: {serverUrl}"));
        };

        _ws.OnMessage += (sender, e) => OnMessage(e.Data);

        _ws.OnError += (sender, e) =>
        {
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogWarning($"[WebSocketClient] 오류: {e.Message}"));
        };

        _ws.OnClose += (sender, e) =>
        {
            _isConnecting = false;
            MainThreadDispatcher.Enqueue(() =>
            {
                Debug.Log($"[WebSocketClient] 연결 해제 (코드: {e.Code})");
                if (_retryCount < maxRetries)
                    StartCoroutine(RetryConnect());
                else
                    Debug.LogError("[WebSocketClient] 최대 재시도 횟수 초과. Python 서버가 실행 중인지 확인하세요.");
            });
        };

        _ws.ConnectAsync();
    }

    private IEnumerator RetryConnect()
    {
        _retryCount++;
        Debug.Log($"[WebSocketClient] {retryDelay}초 후 재연결 시도 ({_retryCount}/{maxRetries})...");
        yield return new WaitForSeconds(retryDelay);
        Connect();
    }

    // ──────────────────────────────────────────────────────────
    // 메시지 처리
    // ──────────────────────────────────────────────────────────

    private void OnMessage(string json)
    {
        try
        {
            var data = JsonConvert.DeserializeObject<FrameData>(json);
            if (data == null) return;

            MainThreadDispatcher.Enqueue(() => HandleData(data));
        }
        catch (Exception ex)
        {
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogWarning($"[WebSocketClient] JSON 파싱 오류: {ex.Message}"));
        }
    }

    private void HandleData(FrameData data)
    {
        if (data.type == "switch_animal")
        {
            animalSwitcher?.SwitchTo(data.animal);
            return;
        }

        if (data.type == "frame")
        {
            // 현재 활성 동물의 컨트롤러를 우선 사용, 없으면 폴백
            var ctrl = animalSwitcher?.GetCurrentController() ?? animalController;
            if (data.hand_detected && data.joints != null)
                ctrl?.ApplyJoints(data.joints);
            else
                ctrl?.SetIdle();
        }
    }
}
