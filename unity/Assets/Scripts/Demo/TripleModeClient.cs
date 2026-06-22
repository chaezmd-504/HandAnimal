// TripleModeClient.cs
// demo_triple.py (ws://localhost:8767) 에서 triple_frame 메시지를 수신하여
// 세 개의 AnimalController 에 각각 direct / keyframe / blend 관절값을 적용한다.
//
// 씬 세팅: HandAvatar/Setup Triple Demo Scene 메뉴 사용

using System;
using System.Collections;
using System.Collections.Generic;
using Newtonsoft.Json;
using UnityEngine;
using WebSocketSharp;

public class TripleModeClient : MonoBehaviour
{
    [Header("서버 설정")]
    [SerializeField] private string serverUrl  = "ws://localhost:8767";
    [SerializeField] private int    maxRetries = 5;
    [SerializeField] private float  retryDelay = 3f;

    [Header("세 모드 컨트롤러 (TripleModeSceneSetup 이 자동 연결)")]
    public AnimalController directCtrl;
    public AnimalController keyframeCtrl;
    public AnimalController blendCtrl;

    private WebSocket _ws;
    private int   _retryCount;
    private bool  _isConnecting;

    // ── Unity 생명주기 ──────────────────────────────────────────

    private void Start()
    {
        // Blend: walkHandBlend=0.65 → Walk 중 Animator 35% + hand 65% → 애니메이션 강도 낮춤
        if (blendCtrl != null)
            blendCtrl.WalkHandBlend = 0.65f;
        Connect();
    }
    private void OnDestroy() { _ws?.Close(); }

    private void OnApplicationQuit() { _ws?.Close(); }

    // ── WebSocket 연결 ─────────────────────────────────────────

    private void Connect()
    {
        if (_isConnecting) return;
        _isConnecting = true;

        _ws = new WebSocket(serverUrl);

        _ws.OnOpen    += (_, __) =>
        {
            _retryCount   = 0;
            _isConnecting = false;
            Debug.Log("[TripleModeClient] 연결 성공: " + serverUrl);
        };

        _ws.OnMessage += (_, e) =>
        {
            if (!e.IsText) return;
            MainThreadDispatcher.Enqueue(() => HandleMessage(e.Data));
        };

        _ws.OnError += (_, e) =>
            Debug.LogWarning("[TripleModeClient] 오류: " + e.Message);

        _ws.OnClose += (_, __) =>
        {
            _isConnecting = false;
            if (_retryCount < maxRetries)
                StartCoroutine(Reconnect());
        };

        _ws.ConnectAsync();
    }

    private IEnumerator Reconnect()
    {
        _retryCount++;
        Debug.Log($"[TripleModeClient] 재연결 시도 {_retryCount}/{maxRetries} ({retryDelay}초 후)");
        yield return new WaitForSeconds(retryDelay);
        Connect();
    }

    // ── 메시지 파싱 ────────────────────────────────────────────

    [Serializable]
    private class TripleFrameData
    {
        public string type;
        public bool   hand_detected;
        public float  speed;
        public Dictionary<string, JointRotation> direct;
        public Dictionary<string, JointRotation> keyframe;
        public Dictionary<string, JointRotation> blend;
        // 트리거 정보 (선택적 — 있으면 해당 프레임에서 트리거 발사)
        public string trigger_anim;
        public float  trigger_duration;
    }

    private void HandleMessage(string json)
    {
        TripleFrameData data;
        try { data = JsonConvert.DeserializeObject<TripleFrameData>(json); }
        catch { return; }

        if (data == null || data.type != "triple_frame") return;

        // ── 트리거 ──
        // Direct : 트리거 없음 — 손 포즈 → 뼈 직접
        // Keyframe: 애니메이션 재생
        // Blend  : 트리거 없음 — 블렌드 엔진 출력 → 뼈 직접 (main.py 방식)
        if (!string.IsNullOrEmpty(data.trigger_anim))
        {
            Debug.Log($"[TripleModeClient] trigger → {data.trigger_anim} ({data.trigger_duration}s)");
            // Keyframe: 100% crisp Attack1 애니메이션
            // Blend: 같은 애니메이션 재생 + triggerHandBlend=0.5 → Slerp(anim, hand, 0.5) = 부드러운 공격
            keyframeCtrl?.PlayTriggerAnim(data.trigger_anim, data.trigger_duration);
            blendCtrl?.PlayTriggerAnim(data.trigger_anim, data.trigger_duration);
        }

        if (!data.hand_detected)
        {
            directCtrl?.SetIdle();
            keyframeCtrl?.SetIdle();
            blendCtrl?.SetIdle();
            directCtrl?.SetWalkActive(false);
            keyframeCtrl?.SetWalkActive(false);
            blendCtrl?.SetWalkActive(false);
            return;
        }

        bool walking = data.speed > 0.001f;
        directCtrl?.SetWalkActive(walking);
        keyframeCtrl?.SetWalkActive(walking);
        // Blend: walk 중 → Walk anim + 70% jb
        //        attack 중(walking=false) → Animator 꺼지고 jb 직접 적용 (Direct 약한 버전)
        blendCtrl?.SetWalkActive(walking);

        Apply(directCtrl,   data.direct);
        Apply(keyframeCtrl, data.keyframe);
        Apply(blendCtrl,    data.blend);
    }

    private static void Apply(AnimalController ctrl,
                               Dictionary<string, JointRotation> joints)
    {
        if (ctrl == null || joints == null) return;
        ctrl.ApplyJoints(joints);
    }
}
