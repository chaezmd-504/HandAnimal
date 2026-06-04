// AnimalController.cs
// [수정 2026-06]
//   Walk 애니메이션 통합:
//   - 손 감지 + 실제 이동 중일 때만 Walk 애니메이션 재생 (locomotion.IsMoving 참조)
//   - locomotion 연결 없으면 손 감지 시 항상 Walk 재생
//   - LateUpdate에서 Walk 포즈 위에 손 입력 블렌딩 (walkHandBlend)
//   - 손 없거나 정지 시 Animator 비활성화 → Direct 매핑으로 폴백
//
// [수정 2026-04]
//   1. Rest-pose 보존: restPose * Quaternion.Euler(delta) 방식으로 적용
//   2. ROM 클램핑
//   3. 다축 회전 지원

using System;
using System.Collections.Generic;
using UnityEngine;

public class AnimalController : MonoBehaviour
{
    [Serializable]
    public class JointEntry
    {
        public string    jointName;
        public Transform jointTransform;

        [Header("회전 축 (복수 선택 가능)")]
        public bool axisX = false;
        public bool axisY = false;
        public bool axisZ = false;

        [Header("각도 범위 (REST 기준 델타)")]
        public float minAngle = -30f;
        public float maxAngle =  30f;

        [NonSerialized] public Quaternion restRotation;
    }

    [Header("관절 매핑")]
    [SerializeField] private List<JointEntry> jointEntries = new List<JointEntry>();

    [Header("Lerp 속도 (클수록 빠름, 60fps 기준)")]
    [SerializeField] [Range(1f, 30f)] private float lerpSpeed = 12f;

    [Header("Idle 애니메이션")]
    [SerializeField] private Animator idleAnimator;

    [Header("Walk 애니메이션")]
    [Tooltip("이동 중 재생할 Walk 상태 이름. 비워두면 항상 Direct 매핑만 사용.")]
    [SerializeField] private string walkAnimName = "Walk";
    [Tooltip("Walk 중 손 입력 반영 비율 (0=애니메이션만, 1=손만)")]
    [Range(0f, 1f)]
    [SerializeField] private float walkHandBlend = 0f;

    [Header("트리거 중 손 블렌딩")]
    [Tooltip("트리거 애니메이션 재생 중 손 입력 반영 비율")]
    [Range(0f, 1f)]
    [SerializeField] private float triggerHandBlend = 0.3f;

    // Walk 활성 여부 (WebSocketClient에서 locomotion.speed 기반으로 설정)
    private bool _walkActive = false;

    [Header("축 / ROM 자동 채우기")]
    [SerializeField] public bool autoInferAxes = true;

    private Dictionary<string, JointEntry> _jointMap;
    private Dictionary<string, Vector3> _targetAngles = new Dictionary<string, Vector3>();
    private bool _isIdle = true;
    private int _applyCount = 0;
    private float _triggerTimer = 0f;

    private bool UseWalkAnim => !string.IsNullOrEmpty(walkAnimName) && idleAnimator != null;

    // 현재 Walk 모드인지 (Animator 활성 + Walk 재생 중)
    private bool _inWalkMode = false;

    private void Awake()
    {
        _jointMap = new Dictionary<string, JointEntry>(StringComparer.Ordinal);
        foreach (var entry in jointEntries)
        {
            if (string.IsNullOrEmpty(entry.jointName) || entry.jointTransform == null)
                continue;
            entry.restRotation = entry.jointTransform.localRotation;
            _jointMap[entry.jointName] = entry;
        }

        if (autoInferAxes)
            Debug.LogWarning("[AnimalController] autoInferAxes=true — skeleton.json 축/ROM을 채워주세요.");

        Debug.Log($"[AnimalController] 관절 {_jointMap.Count}개  walkAnimName='{walkAnimName}'");
    }

    private void Update()
    {
        if (_triggerTimer > 0f)
        {
            _triggerTimer -= Time.deltaTime;
            return;
        }

        if (_isIdle) return;

        // Walk 모드 (Animator 활성): Direct 뼈 제어 스킵 (LateUpdate에서 blend)
        if (_inWalkMode) return;

        // Direct 모드: Update에서 직접 뼈 회전
        foreach (var kv in _targetAngles)
        {
            if (!_jointMap.TryGetValue(kv.Key, out var entry)) continue;
            if (entry.jointTransform == null) continue;

            Quaternion current = entry.jointTransform.localRotation;
            Quaternion target  = BuildRotation(entry, kv.Value);
            entry.jointTransform.localRotation =
                Quaternion.Lerp(current, target, lerpSpeed * Time.deltaTime);
        }
    }

    private void LateUpdate()
    {
        float blend;
        if (_triggerTimer > 0f)
            blend = triggerHandBlend;
        else if (_inWalkMode && !_isIdle)
            blend = walkHandBlend;
        else
            return;

        if (blend <= 0f) return;
        if (_targetAngles.Count == 0) return;

        float t = Mathf.Clamp01(blend * lerpSpeed * Time.deltaTime);
        foreach (var kv in _targetAngles)
        {
            if (!_jointMap.TryGetValue(kv.Key, out var entry)) continue;
            if (entry.jointTransform == null) continue;

            Quaternion animPose = entry.jointTransform.localRotation;
            Quaternion handPose = BuildRotation(entry, kv.Value);
            entry.jointTransform.localRotation = Quaternion.Slerp(animPose, handPose, t);
        }
    }

    public void PlayTriggerAnim(string animName, float durationHint)
    {
        if (idleAnimator == null)
        {
            Debug.LogWarning("[AnimalController] PlayTriggerAnim: idleAnimator가 없습니다.");
            return;
        }

        float clipDuration = durationHint;
        var clips = idleAnimator.runtimeAnimatorController?.animationClips;
        if (clips != null)
        {
            foreach (var clip in clips)
            {
                if (clip.name == animName) { clipDuration = clip.length; break; }
            }
        }

        idleAnimator.enabled = true;
        idleAnimator.Play(animName, 0, 0f);
        _triggerTimer  = clipDuration;
        _isIdle        = true;
        _inWalkMode    = false;
        Debug.Log($"[AnimalController] 트리거 재생: {animName}  ({clipDuration:F2}s)");
    }

    public void ApplyJoints(Dictionary<string, JointRotation> joints)
    {
        if (_triggerTimer > 0f)
        {
            foreach (var kv in joints)
                _targetAngles[kv.Key] = new Vector3(kv.Value.x, kv.Value.y, kv.Value.z);
            return;
        }

        bool wasIdle  = _isIdle;
        _isIdle = false;

        bool shouldWalk = UseWalkAnim && _walkActive;

        if (idleAnimator != null)
        {
            if (shouldWalk)
            {
                // Walk 모드: Animator 켜고 Walk 재생
                if (!idleAnimator.enabled) idleAnimator.enabled = true;
                var info = idleAnimator.GetCurrentAnimatorStateInfo(0);
                if (!info.IsName(walkAnimName))
                {
                    idleAnimator.Play(walkAnimName);
                    if (!_inWalkMode) Debug.Log($"[AnimalController] Walk 시작: '{walkAnimName}'");
                }
                _inWalkMode = true;
            }
            else
            {
                // Direct 모드: Animator 끔
                if (idleAnimator.enabled) idleAnimator.enabled = false;

                if (_inWalkMode || wasIdle)
                {
                    // Walk→Direct 전환 시 restRotation 재캡처
                    foreach (var entry in _jointMap.Values)
                        if (entry.jointTransform != null)
                            entry.restRotation = entry.jointTransform.localRotation;
                    Debug.Log("[AnimalController] Direct 모드: restRotation 재캡처");
                }
                _inWalkMode = false;
            }
        }

        foreach (var kv in joints)
            _targetAngles[kv.Key] = new Vector3(kv.Value.x, kv.Value.y, kv.Value.z);

        _applyCount++;
        if (_applyCount <= 5)
        {
            var sbApplied = new System.Text.StringBuilder();
            var sbMissing = new System.Text.StringBuilder();
            foreach (var kv in joints)
            {
                if (_jointMap.ContainsKey(kv.Key))
                    sbApplied.Append($"{kv.Key}=({kv.Value.x:F1},{kv.Value.y:F1},{kv.Value.z:F1}) ");
                else
                    sbMissing.Append($"{kv.Key} ");
            }
            Debug.Log($"[AnimalController] ApplyJoints #{_applyCount}\n" +
                      $"  ✅ ({CountApplied(joints)}개): {sbApplied}\n" +
                      $"  ❌ ({CountMissing(joints)}개): {sbMissing}");
        }
    }

    private int CountApplied(Dictionary<string, JointRotation> joints)
    { int n = 0; foreach (var k in joints.Keys) if (_jointMap.ContainsKey(k)) n++; return n; }

    private int CountMissing(Dictionary<string, JointRotation> joints)
    { int n = 0; foreach (var k in joints.Keys) if (!_jointMap.ContainsKey(k)) n++; return n; }

    /// <summary>WebSocketClient에서 locomotion.speed &gt; 0 여부를 전달해 Walk 재생 제어.</summary>
    public void SetWalkActive(bool active)
    {
        _walkActive = active;
    }

    public void SetIdle()
    {
        if (_triggerTimer > 0f) return;
        if (_isIdle) return;
        _isIdle = true;

        if (idleAnimator != null && idleAnimator.enabled)
            idleAnimator.enabled = false;

        _inWalkMode = false;
    }

    private static Quaternion BuildRotation(JointEntry entry, Vector3 rot)
    {
        float ax = entry.axisX ? Mathf.Clamp(rot.x, entry.minAngle, entry.maxAngle) : 0f;
        float ay = entry.axisY ? Mathf.Clamp(rot.y, entry.minAngle, entry.maxAngle) : 0f;
        float az = entry.axisZ ? Mathf.Clamp(rot.z, entry.minAngle, entry.maxAngle) : 0f;

        return entry.restRotation * Quaternion.Euler(ax, ay, az);
    }

    public Dictionary<string, JointRotation> GetCurrentAngles()
    {
        var result = new Dictionary<string, JointRotation>();
        foreach (var entry in jointEntries)
        {
            if (entry.jointTransform == null) continue;
            Quaternion delta = Quaternion.Inverse(entry.restRotation) * entry.jointTransform.localRotation;
            Vector3 e = delta.eulerAngles;
            float x = e.x > 180f ? e.x - 360f : e.x;
            float y = e.y > 180f ? e.y - 360f : e.y;
            float z = e.z > 180f ? e.z - 360f : e.z;
            result[entry.jointName] = new JointRotation
            {
                x = Mathf.Round(x * 10f) / 10f,
                y = Mathf.Round(y * 10f) / 10f,
                z = Mathf.Round(z * 10f) / 10f,
            };
        }
        return result;
    }

    public void SetJointAxes(string jointName,
                              bool axisX = false, bool axisY = false, bool axisZ = false,
                              float minAngle = -45f, float maxAngle = 45f)
    {
        if (!_jointMap.TryGetValue(jointName, out var entry))
        { Debug.LogWarning($"[AnimalController] 관절 없음: {jointName}"); return; }
        entry.axisX = axisX; entry.axisY = axisY; entry.axisZ = axisZ;
        entry.minAngle = minAngle; entry.maxAngle = maxAngle;
    }

    public void ResetToRestPose()
    {
        foreach (var entry in jointEntries)
            if (entry.jointTransform != null)
                entry.jointTransform.localRotation = entry.restRotation;
        _targetAngles.Clear();
        Debug.Log("[AnimalController] Rest-pose로 복귀");
    }
}
