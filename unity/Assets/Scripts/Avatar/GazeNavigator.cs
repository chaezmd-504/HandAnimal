// GazeNavigator.cs
// Python gaze_sender.py 에서 전송된 iris UV 좌표를 받아
// 카메라 ViewportRay → 지면 Raycast → 드웰(dwell) 확인 → 대상 오브젝트 이동
//
// ★ 동물 파이프라인과 완전히 분리된 독립 컴포넌트 ★
//
// 사용 방법:
//   1. 씬에 빈 GameObject 추가 → GazeReceiver + GazeNavigator 컴포넌트 부착
//   2. GazeReceiver: 포트 설정 (기본 8766), GazeNavigator 슬롯 연결
//   3. GazeNavigator: TargetObject (움직일 오브젝트), GroundLayer, Camera 설정
//   4. (선택) GazeIndicator / ConfirmedIndicator 마커 오브젝트 연결
//
// 드웰 방식:
//   - 시선이 dwellRadius(m) 이내에 dwellTime(초) 동안 머물면 목표 확정
//   - 확정 시 targetObject 가 확정 지점으로 이동

using UnityEngine;

public class GazeNavigator : MonoBehaviour
{
    [Header("이동 대상")]
    [Tooltip("시선으로 이동시킬 오브젝트. 비워두면 로그만 출력.")]
    [SerializeField] private Transform targetObject;
    [Tooltip("목표 이동 시 lerp 속도 (클수록 빠름)")]
    [SerializeField] private float moveSpeed = 5f;

    [Header("Raycast 설정")]
    [Tooltip("시선 Raycast 기준 카메라. 비워두면 Camera.main 사용.")]
    [SerializeField] private Camera    gazeCamera;
    [Tooltip("지면 레이어 마스크 (충돌 대상)")]
    [SerializeField] private LayerMask groundLayer = ~0;
    [Tooltip("Raycast 최대 거리 (m)")]
    [SerializeField] private float     maxRayDist  = 300f;

    [Header("드웰 설정")]
    [Tooltip("목표 확정에 필요한 시선 유지 시간 (초)")]
    [SerializeField] private float dwellTime   = 1.0f;
    [Tooltip("드웰 유지 판정 반경 (m)")]
    [SerializeField] private float dwellRadius = 1.5f;

    [Header("시각 마커")]
    [Tooltip("시선 현재 위치 표시 오브젝트 (없어도 됨)")]
    [SerializeField] private GameObject gazeIndicator;
    [Tooltip("목표 확정 시 표시 오브젝트 (없어도 됨)")]
    [SerializeField] private GameObject confirmedIndicator;

    [Header("디버그")]
    [SerializeField] private bool showLog = true;

    // ── 내부 상태 ─────────────────────────────────────────────
    private const float GazeEma = 0.2f;
    private float _smoothX     = 0.5f;
    private float _smoothY     = 0.5f;

    private bool    _hasDwell  = false;
    private Vector3 _dwellPt;
    private float   _dwellT    = 0f;

    private Vector3 _navTarget;
    private bool    _hasTarget = false;

    private int _missCnt       = 0;
    private const int MissReset = 10;

    // ──────────────────────────────────────────────────────────
    // 외부 API — GazeReceiver 에서 매 프레임 호출
    // ──────────────────────────────────────────────────────────

    public void OnGazeUpdate(float gazeX, float gazeY)
    {
        _missCnt = 0;
        _smoothX = GazeEma * gazeX + (1f - GazeEma) * _smoothX;
        _smoothY = GazeEma * gazeY + (1f - GazeEma) * _smoothY;

        Camera cam = gazeCamera != null ? gazeCamera : Camera.main;
        if (cam == null) return;

        // Unity viewport y=0이 아래쪽 → gaze_y=0이 위쪽이므로 반전
        Ray ray = cam.ViewportPointToRay(new Vector3(_smoothX, 1f - _smoothY, 0f));

        if (!Physics.Raycast(ray, out RaycastHit hit, maxRayDist, groundLayer))
        {
            ResetDwell();
            SetGazeIndicator(false, Vector3.zero);
            return;
        }

        Vector3 hitPt = hit.point;
        SetGazeIndicator(true, hitPt);

        // 드웰 타이머
        if (!_hasDwell)
        {
            _dwellPt = hitPt;
            _hasDwell = true;
            _dwellT   = 0f;
        }
        else if (Vector3.Distance(hitPt, _dwellPt) > dwellRadius)
        {
            _dwellPt = hitPt;
            _dwellT  = 0f;
        }
        else
        {
            _dwellT += Time.deltaTime;

            // 진행도 마커 크기에 반영
            if (gazeIndicator != null)
            {
                float p = Mathf.Clamp01(_dwellT / dwellTime);
                gazeIndicator.transform.localScale = Vector3.one * (0.3f + 0.7f * p);
            }

            if (_dwellT >= dwellTime)
                ConfirmTarget(_dwellPt);
        }
    }

    public void OnGazeLost()
    {
        _missCnt++;
        if (_missCnt >= MissReset)
        {
            ResetDwell();
            SetGazeIndicator(false, Vector3.zero);
        }
    }

    // ──────────────────────────────────────────────────────────
    // Unity 업데이트 — 목표 지점으로 오브젝트 이동
    // ──────────────────────────────────────────────────────────

    private void Update()
    {
        if (!_hasTarget || targetObject == null) return;

        Vector3 cur  = targetObject.position;
        Vector3 dest = new Vector3(_navTarget.x, cur.y, _navTarget.z);  // Y 고정

        targetObject.position = Vector3.MoveTowards(cur, dest, moveSpeed * Time.deltaTime);

        if (Vector3.Distance(cur, dest) < 0.05f)
        {
            _hasTarget = false;
            if (showLog) Debug.Log("[GazeNavigator] 목표 도착");
        }
    }

    // ──────────────────────────────────────────────────────────
    // 내부 헬퍼
    // ──────────────────────────────────────────────────────────

    private void ConfirmTarget(Vector3 pt)
    {
        _navTarget = pt;
        _hasTarget = true;
        _dwellT    = 0f;
        _hasDwell  = false;

        if (confirmedIndicator != null)
        {
            confirmedIndicator.SetActive(true);
            confirmedIndicator.transform.position = pt + Vector3.up * 0.15f;
            CancelInvoke(nameof(HideConfirmed));
            Invoke(nameof(HideConfirmed), 3f);
        }

        if (showLog)
            Debug.Log($"[GazeNavigator] 목표 확정 ({pt.x:F1}, {pt.z:F1})  dwell={dwellTime}s");
    }

    private void HideConfirmed()
    {
        if (confirmedIndicator != null) confirmedIndicator.SetActive(false);
    }

    private void ResetDwell()
    {
        _hasDwell = false;
        _dwellT   = 0f;
        if (gazeIndicator != null) gazeIndicator.transform.localScale = Vector3.one * 0.3f;
    }

    private void SetGazeIndicator(bool active, Vector3 pos)
    {
        if (gazeIndicator == null) return;
        gazeIndicator.SetActive(active);
        if (active) gazeIndicator.transform.position = pos + Vector3.up * 0.05f;
    }

    // ──────────────────────────────────────────────────────────
    // Gizmos
    // ──────────────────────────────────────────────────────────

    private void OnDrawGizmosSelected()
    {
        if (!_hasDwell) return;
        float p = Mathf.Clamp01(_dwellT / (dwellTime + 0.001f));
        Gizmos.color = new Color(0.5f, 1f, 0.5f, 0.3f + 0.5f * p);
        Gizmos.DrawWireSphere(_dwellPt, dwellRadius);
    }
}
