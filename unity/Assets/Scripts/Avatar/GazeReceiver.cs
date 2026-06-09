// GazeReceiver.cs
// gaze_sender.py 에서 전송하는 {"gaze_x": ..., "gaze_y": ..., "valid": ...} 를 수신해
// GazeNavigator 에 전달하는 독립 WebSocket 클라이언트.
//
// 동물 파이프라인(WebSocketClient.cs)과 별도 포트를 사용한다 (기본 8766).

using System;
using System.Collections;
using Newtonsoft.Json;
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
    [SerializeField] private int    maxRetries  = 5;
    [SerializeField] private float  retryDelay  = 3f;

    [Header("연결 컴포넌트")]
    [SerializeField] private GazeNavigator gazeNavigator;

    private WebSocket _ws;
    private int  _retryCount;
    private bool _isConnecting;

    private void Start()  => Connect();
    private void OnDestroy()         => _ws?.Close();
    private void OnApplicationQuit() => _ws?.Close();

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
        yield return new WaitForSeconds(retryDelay);
        Connect();
    }

    private void OnMessage(string json)
    {
        try
        {
            var data = JsonConvert.DeserializeObject<GazeData>(json);
            if (data == null) return;
            MainThreadDispatcher.Enqueue(() => HandleData(data));
        }
        catch (Exception ex)
        {
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogWarning($"[GazeReceiver] JSON 파싱 오류: {ex.Message}"));
        }
    }

    private void HandleData(GazeData data)
    {
        if (gazeNavigator == null) return;
        if (data.valid)
            gazeNavigator.OnGazeUpdate(data.gaze_x, data.gaze_y);
        else
            gazeNavigator.OnGazeLost();
    }
}
