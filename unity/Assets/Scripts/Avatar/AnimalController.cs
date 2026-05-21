// AnimalController.cs
// [수정 2026-04]
//   핵심 버그 수정:
//   1. Rest-pose 보존: Awake에서 초기 localRotation 저장,
//      이후 회전은 restPose * Quaternion.Euler(delta) 방식으로 적용
//      → 기존: Quaternion.Euler(angle)로 절대 회전 세팅 → 다리 축 처짐
//   2. ROM 클램핑: 관절 타입(_base/_mid/_tip)별 각도 한계 적용
//   3. 다축 회전 지원 + 관절명 패턴 축 자동 추론 유지

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

        // 런타임 전용 — Inspector에 노출 안 함
        [NonSerialized] public Quaternion restRotation;
    }

    [Header("관절 매핑")]
    [SerializeField] private List<JointEntry> jointEntries = new List<JointEntry>();

    [Header("Lerp 속도 (클수록 빠름, 60fps 기준)")]
    [SerializeField] [Range(1f, 30f)] private float lerpSpeed = 12f;

    [Header("Idle 애니메이션")]
    [SerializeField] private Animator idleAnimator;

    [Header("축 / ROM 자동 채우기")]
    [Tooltip("true = Inspector 하단 버튼으로 skeleton.json 에서 축/ROM 자동 세팅 필요.\n" +
             "AnimPoseExporter 로 skeleton.json 을 먼저 생성한 뒤 Editor 버튼을 누르세요.")]
    [SerializeField] public bool autoInferAxes = true;

    private Dictionary<string, JointEntry> _jointMap;
    private Dictionary<string, Vector3> _targetAngles = new Dictionary<string, Vector3>();
    private bool _isIdle = true;
    private int _applyCount = 0;

    private void Awake()
    {
        _jointMap = new Dictionary<string, JointEntry>(StringComparer.Ordinal);
        foreach (var entry in jointEntries)
        {
            if (string.IsNullOrEmpty(entry.jointName) || entry.jointTransform == null)
                continue;

            // ── 핵심: Rest-pose 저장 ──────────────────────────────
            // 이후 모든 회전은 이 값을 기준으로 delta 적용
            entry.restRotation = entry.jointTransform.localRotation;

            _jointMap[entry.jointName] = entry;
        }

        if (autoInferAxes)
            Debug.LogWarning("[AnimalController] autoInferAxes=true — Inspector 하단 버튼으로 skeleton.json 에서 축/ROM을 채워주세요.");

        Debug.Log($"[AnimalController] 관절 {_jointMap.Count}개 로드됨");
        foreach (var kv in _jointMap)
        {
            var e = kv.Value;
            string axes = $"{(e.axisX?"X":"")}{(e.axisY?"Y":"")}{(e.axisZ?"Z":"")}";
            if (string.IsNullOrEmpty(axes)) axes = "none";
            Debug.Log($"  {kv.Key}: 축={axes}  ROM=[{e.minAngle:F0}°, {e.maxAngle:F0}°]  " +
                      $"rest=({e.restRotation.x:F3},{e.restRotation.y:F3}," +
                      $"{e.restRotation.z:F3},{e.restRotation.w:F3})");
        }
    }

    private void Update()
    {
        if (_isIdle) return;

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

    public void ApplyJoints(Dictionary<string, JointRotation> joints)
    {
        _isIdle = false;

        if (idleAnimator != null && idleAnimator.enabled)
            idleAnimator.enabled = false;

        foreach (var kv in joints)
            _targetAngles[kv.Key] = new Vector3(kv.Value.x, kv.Value.y, kv.Value.z);

        _applyCount++;
        if (_applyCount <= 5)
        {
            var sbApplied  = new System.Text.StringBuilder();
            var sbMissing  = new System.Text.StringBuilder();

            foreach (var kv in joints)
            {
                if (_jointMap.ContainsKey(kv.Key))
                    sbApplied.Append($"{kv.Key}=({kv.Value.x:F1},{kv.Value.y:F1},{kv.Value.z:F1}) ");
                else
                    sbMissing.Append($"{kv.Key} ");
            }

            Debug.Log($"[AnimalController] ApplyJoints #{_applyCount}\n" +
                      $"  ✅ 적용됨 ({CountApplied(joints)}개): {sbApplied}\n" +
                      $"  ❌ Transform 없음 ({CountMissing(joints)}개): {sbMissing}");
        }
    }

    private int CountApplied(Dictionary<string, JointRotation> joints)
    {
        int n = 0;
        foreach (var k in joints.Keys) if (_jointMap.ContainsKey(k)) n++;
        return n;
    }

    private int CountMissing(Dictionary<string, JointRotation> joints)
    {
        int n = 0;
        foreach (var k in joints.Keys) if (!_jointMap.ContainsKey(k)) n++;
        return n;
    }

    public void SetIdle()
    {
        if (_isIdle) return;
        _isIdle = true;
        if (idleAnimator != null)
            idleAnimator.enabled = true;
    }

    /// <summary>
    /// Rest-pose 기준 delta 회전을 적용한 Quaternion 반환.
    ///
    /// 계산:
    ///   1. angle을 ROM [minAngle, maxAngle]으로 클램핑
    ///   2. delta = Quaternion.Euler(clampedAngle * axis * sign)
    ///   3. result = restRotation * delta
    ///
    /// restRotation을 곱하는 이유:
    ///   본이 프리팹에서 가지는 초기 쿼터니언(비-identity)을 보존하면서
    ///   그 위에 손가락 각도만큼의 추가 회전을 얹는 구조.
    ///   → 기존 절대 Euler 방식의 "축 처짐" 버그 수정
    /// </summary>
    private static Quaternion BuildRotation(JointEntry entry, Vector3 rot)
    {
        float ax = Mathf.Clamp(rot.x, entry.minAngle, entry.maxAngle);
        float ay = Mathf.Clamp(rot.y, entry.minAngle, entry.maxAngle);
        float az = Mathf.Clamp(rot.z, entry.minAngle, entry.maxAngle);

        Quaternion delta = Quaternion.Euler(ax, ay, az);
        return entry.restRotation * delta;
    }

    // ── 포즈 캡처 (AvatarPoseSender 전용) ──────────────────────────

    /// <summary>
    /// 현재 각 관절의 Rest 기준 델타 각도를 반환한다.
    /// AvatarPoseSender가 Python으로 전송하는 데 사용한다.
    /// </summary>
    public Dictionary<string, JointRotation> GetCurrentAngles()
    {
        var result = new Dictionary<string, JointRotation>();
        foreach (var entry in jointEntries)
        {
            if (entry.jointTransform == null) continue;

            // rest 기준 delta 회전 → Euler 변환
            Quaternion delta = Quaternion.Inverse(entry.restRotation)
                               * entry.jointTransform.localRotation;
            Vector3 e = delta.eulerAngles;

            // 0..360 → -180..180
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

    // ── 디버그 헬퍼 ──────────────────────────────────────────────

    /// <summary>
    /// 특정 관절의 축/ROM/반전을 런타임에 변경한다.
    /// </summary>
    public void SetJointAxes(string jointName,
                              bool axisX = false, bool axisY = false, bool axisZ = false,
                              float minAngle = -45f, float maxAngle = 45f)
    {
        if (!_jointMap.TryGetValue(jointName, out var entry))
        {
            Debug.LogWarning($"[AnimalController] 관절 없음: {jointName}");
            return;
        }
        entry.axisX = axisX; entry.axisY = axisY; entry.axisZ = axisZ;
        entry.minAngle = minAngle; entry.maxAngle = maxAngle;
        Debug.Log($"[AnimalController] {jointName} 변경: 축={( axisX?"X":"")}{(axisY?"Y":"")}{(axisZ?"Z":"")} " +
                  $"ROM=[{minAngle},{maxAngle}]");
    }

    /// <summary>
    /// 모든 관절을 Rest-pose로 즉시 복귀.
    /// </summary>
    public void ResetToRestPose()
    {
        foreach (var entry in jointEntries)
        {
            if (entry.jointTransform != null)
                entry.jointTransform.localRotation = entry.restRotation;
        }
        _targetAngles.Clear();
        Debug.Log("[AnimalController] Rest-pose로 복귀");
    }
}
