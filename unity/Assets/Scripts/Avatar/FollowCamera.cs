// FollowCamera.cs
// 대상 오브젝트 뒤에서 따라가는 카메라.
// Main Camera에 붙이고 Target에 Spider 루트 오브젝트를 연결한다.

using UnityEngine;

public class FollowCamera : MonoBehaviour
{
    [Header("추적 대상")]
    [SerializeField] private Transform target;

    [Header("위치 오프셋 (대상 로컬 기준)")]
    [Tooltip("뒤로 얼마나 떨어질지 (양수 = 뒤쪽)")]
    [SerializeField] private float distanceBack = 5f;
    [Tooltip("위로 얼마나 올라갈지")]
    [SerializeField] private float height = 3f;

    [Header("부드러움")]
    [Tooltip("위치 추적 속도 (높을수록 빠르게 따라감)")]
    [SerializeField] private float positionSmoothing = 6f;
    [Tooltip("회전 추적 속도")]
    [SerializeField] private float rotationSmoothing = 6f;

    /// <summary>AnimalSwitcher가 동물 전환 시 호출해 추적 대상을 교체한다.</summary>
    public void SetTarget(Transform newTarget)
    {
        target = newTarget;
    }

    private void LateUpdate()
    {
        if (target == null) return;

        // 대상 뒤 + 위 위치 계산
        Vector3 desiredPos = target.position
                           - target.forward * distanceBack
                           + Vector3.up * height;

        // 부드럽게 이동
        transform.position = Vector3.Lerp(
            transform.position, desiredPos,
            positionSmoothing * Time.deltaTime
        );

        // 대상을 바라보도록 회전
        Vector3 lookTarget = target.position + Vector3.up * (height * 0.3f);
        Quaternion desiredRot = Quaternion.LookRotation(
            (lookTarget - transform.position).normalized
        );
        transform.rotation = Quaternion.Slerp(
            transform.rotation, desiredRot,
            rotationSmoothing * Time.deltaTime
        );
    }
}
