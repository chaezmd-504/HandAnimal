"""
test_head_dir.py — 머리 방향 인식 단독 테스트
실행: python python/scripts/test_head_dir.py
종료: q 키
"""
import cv2

HEAD_DEADZONE = 0.08
HEAD_SCALE    = 3.0

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

cap = cv2.VideoCapture(0)
ref_x = 0.5   # 기준 x (스페이스바로 재설정)

print("실행 중... 스페이스바=기준 재설정, q=종료")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    h, w = frame.shape[:2]
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
    )

    yaw = 0.0
    direction = "NO FACE"
    color = (80, 80, 80)

    if len(faces) > 0:
        fx, fy, fw, fh = faces[0]
        face_cx = (fx + fw / 2.0) / w
        offset  = face_cx - ref_x

        # 얼굴 박스
        cv2.rectangle(frame, (fx, fy), (fx+fw, fy+fh), (0, 220, 100), 2)
        # 중심점
        cv2.circle(frame, (fx + fw//2, fy + fh//2), 5, (0, 220, 100), -1)

        if abs(offset) < HEAD_DEADZONE:
            direction = "STRAIGHT"
            color = (180, 180, 180)
        else:
            sign = 1.0 if offset > 0 else -1.0
            yaw  = (abs(offset) - HEAD_DEADZONE) * HEAD_SCALE * sign * -1.0
            if yaw > 0:
                direction = f"TURNING RIGHT  ({yaw:+.2f})"
                color = (255, 160, 40)
            else:
                direction = f"TURNING LEFT   ({yaw:+.2f})"
                color = (40, 180, 255)

        # offset 바
        bar_x = int(w / 2 + offset * w * 0.8)
        cv2.line(frame, (w//2, h-40), (bar_x, h-40), color, 6)
        cv2.circle(frame, (bar_x, h-40), 8, color, -1)
        cv2.line(frame, (w//2, h-55), (w//2, h-25), (100, 100, 100), 2)

        print(f"\r[HEAD] face_cx={face_cx:.3f}  ref={ref_x:.3f}  "
              f"offset={offset:+.3f}  yaw={yaw:+.2f}  → {direction[:14]}    ", end="")
    else:
        print(f"\r[HEAD] 얼굴 미감지                                              ", end="")

    # 기준선
    cv2.line(frame, (int(ref_x * w), 0), (int(ref_x * w), h),
             (60, 60, 180), 1)

    cv2.putText(frame, direction, (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)
    cv2.putText(frame, f"ref_x={ref_x:.3f}  [SPACE] reset",
                (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1)

    cv2.imshow("Head Direction Test", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == ord(" "):
        if len(faces) > 0:
            ref_x = (faces[0][0] + faces[0][2] / 2.0) / w
            print(f"\n[HEAD] 기준 x={ref_x:.3f} 재설정")
        else:
            print("\n[HEAD] 얼굴 없음 — 재설정 실패")

cap.release()
cv2.destroyAllWindows()
