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
public class LocomotionData
{
    public float speed;
    public float yaw_delta;
    public float cursor;
    public bool  valid;
}

[Serializable]
public class FrameData
{
    public string type;
    public string animal;
    public System.Collections.Generic.Dictionary<string, JointRotation> joints;
    public bool          hand_detected;
    public string        gesture;
    public LocomotionData locomotion;   // null if --locomotion not enabled on Python side
    // trigger 전용
    public string anim;
    public float  duration;
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
    [Tooltip("(선택) 로코모션 컴포넌트 — Python --locomotion 플래그 사용 시 연결")]
    [SerializeField] private AnimalLocomotion animalLocomotion;

    private WebSocket _ws;
    private int _retryCount;
    private bool _isConnecting;

    // ──────────────────────────────────────────────────────────
    // Unity 생명주기
    // ──────────────────────────────────────────────────────────

    private void Start()
    {
        // AnimalSwitcher의 초기 동물 locomotion을 가져온다.
        // Inspector에 직접 연결하지 않아도 AnimalSwitcher 경유로 자동 설정됨.
        if (animalLocomotion == null && animalSwitcher != null)
            animalLocomotion = animalSwitcher.GetCurrentLocomotion();

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
            // 동물 전환 시 현재 활성 동물의 locomotion 컴포넌트로 교체
            animalLocomotion = animalSwitcher?.GetCurrentLocomotion() ?? animalLocomotion;
            return;
        }

        if (data.type == "trigger")
        {
            var ctrl = animalSwitcher?.GetCurrentController() ?? animalController;
            ctrl?.PlayTriggerAnim(data.anim, data.duration > 0f ? data.duration : 2.0f);
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

            // 로코모션 적용 (Python --locomotion 플래그 활성 시에만 data.locomotion 존재)
            if (animalLocomotion != null && data.locomotion != null)
            {
                animalLocomotion.ApplyLocomotion(
                    data.locomotion.speed,
                    data.locomotion.yaw_delta,
                    data.locomotion.valid
                );
            }
        }
    }
}
