// AvatarPoseSender.cs
// 포즈 캡처 도구 — 방법 A (Unity → Python 자동 추출)
//
// 사용법:
//   1) Python: python scripts/extract_avatar_poses.py --mode ws
//   2) Unity Inspector에서 "연결" 클릭 (ws://localhost:8766)
//   3) 아바타를 원하는 포즈로 배치한 뒤 "현재 포즈 캡처" 클릭 (반복)
//   4) "저장 (현재 동물)" 또는 "저장 (전체 동물)" 클릭
//
// 의존성:
//   - WebSocketSharp.dll  → Assets/Plugins/
//   - Newtonsoft.Json     → Package Manager
//   - MainThreadDispatcher (씬에 배치)
//   - AnimalController (같은 GameObject 또는 Inspector에서 연결)

using System.Collections.Generic;
using Newtonsoft.Json;
using UnityEngine;
using WebSocketSharp;

public class AvatarPoseSender : MonoBehaviour
{
    [Header("서버 설정")]
    [SerializeField] private string serverUrl = "ws://localhost:8766";

    [Header("동물 설정")]
    [SerializeField] private string animalName = "spider";
    [SerializeField] private AnimalController animalController;

    [Header("상태 (읽기 전용)")]
    [SerializeField] private bool   isConnected     = false;
    [SerializeField] private int    capturedCount   = 0;
    [SerializeField] private string lastResponse    = "";

    private WebSocket _ws;

    // ──────────────────────────────────────────────────────────
    // Unity 생명주기
    // ──────────────────────────────────────────────────────────

    private void OnDestroy()
    {
        Disconnect();
    }

    // ──────────────────────────────────────────────────────────
    // Inspector ContextMenu 버튼
    // ──────────────────────────────────────────────────────────

    [ContextMenu("① 연결")]
    public void Connect()
    {
        Disconnect();
        _ws = new WebSocket(serverUrl);

        _ws.OnOpen += (s, e) =>
            MainThreadDispatcher.Enqueue(() =>
            {
                isConnected = true;
                Debug.Log($"[AvatarPoseSender] Python 서버 연결: {serverUrl}");
            });

        _ws.OnClose += (s, e) =>
            MainThreadDispatcher.Enqueue(() =>
            {
                isConnected = false;
                Debug.Log("[AvatarPoseSender] 연결 해제");
            });

        _ws.OnError += (s, e) =>
            MainThreadDispatcher.Enqueue(() =>
                Debug.LogError($"[AvatarPoseSender] 오류: {e.Message}"));

        _ws.OnMessage += (s, e) =>
            MainThreadDispatcher.Enqueue(() =>
            {
                lastResponse = e.Data;
                Debug.Log($"[AvatarPoseSender] 응답: {e.Data}");
            });

        _ws.ConnectAsync();
    }

    [ContextMenu("연결 해제")]
    public void Disconnect()
    {
        if (_ws == null) return;
        _ws.Close();
        _ws = null;
        isConnected = false;
    }

    [ContextMenu("② 현재 포즈 캡처")]
    public void CapturePose()
    {
        if (!CheckConnected()) return;

        var joints = animalController != null
            ? animalController.GetCurrentAngles()
            : new Dictionary<string, JointRotation>();

        if (joints.Count == 0)
        {
            Debug.LogWarning("[AvatarPoseSender] 관절 없음. AnimalController를 확인하세요.");
            return;
        }

        var payload = new Dictionary<string, object>
        {
            ["type"]   = "capture_pose",
            ["animal"] = animalName,
            ["joints"] = joints,
        };
        Send(payload);

        capturedCount++;
        Debug.Log($"[AvatarPoseSender] 포즈 #{capturedCount} 전송 ({joints.Count}개 관절)");
    }

    [ContextMenu("③ 저장 (현재 동물)")]
    public void SaveCurrentAnimal()
    {
        if (!CheckConnected()) return;
        Send(new Dictionary<string, string> { ["type"] = "save", ["animal"] = animalName });
        Debug.Log($"[AvatarPoseSender] 저장 요청: {animalName} ({capturedCount}개 포즈)");
    }

    [ContextMenu("③ 저장 (전체 동물)")]
    public void SaveAll()
    {
        if (!CheckConnected()) return;
        Send(new Dictionary<string, string> { ["type"] = "save_all" });
        Debug.Log("[AvatarPoseSender] 전체 저장 요청");
    }

    [ContextMenu("캡처 목록 초기화")]
    public void ClearCaptures()
    {
        if (!CheckConnected()) return;
        Send(new Dictionary<string, string> { ["type"] = "clear", ["animal"] = animalName });
        capturedCount = 0;
        Debug.Log($"[AvatarPoseSender] {animalName} 캡처 목록 초기화");
    }

    [ContextMenu("상태 확인")]
    public void QueryStatus()
    {
        if (!CheckConnected()) return;
        Send(new Dictionary<string, string> { ["type"] = "status" });
    }

    // ──────────────────────────────────────────────────────────
    // 내부 유틸리티
    // ──────────────────────────────────────────────────────────

    private void Send(object payload)
    {
        _ws.Send(JsonConvert.SerializeObject(payload));
    }

    private bool CheckConnected()
    {
        if (_ws != null && isConnected) return true;
        Debug.LogWarning("[AvatarPoseSender] Python 서버에 연결되지 않았습니다. '① 연결'을 먼저 클릭하세요.");
        return false;
    }
}
