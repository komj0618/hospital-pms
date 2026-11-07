import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

admission_bp = Blueprint('admission', __name__)

@admission_bp.route('/')
def admission_list():
    """병상 현황 대시보드, 입원 대기 환자, 현재 입원 중인 환자 목록 조회"""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 병동/병상 현황 대시보드 데이터 (ROOMS, BEDS 조인)
        cursor.execute("""
            SELECT R.ROOM_NO, R.FLOOR, B.BED_ID, B.BED_NO, 
                   NVL(B.STATUS, '사용가능') AS BED_STATUS, P.PATIENT_NAME
            FROM ROOMS R
            JOIN BEDS B ON R.ROOM_ID = B.ROOM_ID
            LEFT JOIN ADMISSIONS AD ON B.BED_ID = AD.BED_ID AND AD.DISCHARGE_DT IS NULL
            LEFT JOIN MEDICAL_RECORDS MR ON AD.RECORD_ID = MR.RECORD_ID
            LEFT JOIN APPOINTMENTS APP ON MR.RECEPTION_ID = APP.RECEPTION_ID
            LEFT JOIN PATIENTS P ON APP.PATIENT_ID = P.PATIENT_ID
            ORDER BY R.FLOOR, R.ROOM_NO, B.BED_NO
        """)
        cols = [c[0] for c in cursor.description]
        beds_raw = [dict(zip(cols, row)) for row in cursor.fetchall()]
        
        # 룸(ROOM)별로 병상(BED) 그룹화
        rooms = {}
        for b in beds_raw:
            room_val = str(b['ROOM_NO']).strip()
            if not room_val.endswith('호'):
                room_val += '호'
            room_name = f"{b['FLOOR']}층 {room_val}"
            
            if room_name not in rooms:
                rooms[room_name] = []
            rooms[room_name].append(b)

        # 2. 입원 대기 환자 목록 (진료실에서 입원 지시를 내려 '입원대기' 상태인 환자만 조회)
        cursor.execute("""
            SELECT A.RECEPTION_ID, P.PATIENT_NAME, P.GENDER, 
                   TO_CHAR(P.BIRTH_DATE, 'YYYY-MM-DD') AS BIRTH_DATE,
                   MR.RECORD_ID, MR.DIAGNOSIS, D.DOC_NAME
            FROM APPOINTMENTS A
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            JOIN MEDICAL_RECORDS MR ON A.RECEPTION_ID = MR.RECEPTION_ID
            JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            WHERE A.STATUS = '입원대기'
            ORDER BY A.RECEPT_DT DESC
        """)
        cols = [c[0] for c in cursor.description]
        pending_patients = [dict(zip(cols, row)) for row in cursor.fetchall()]

        # 3. 현재 재원 중인(입원) 환자 목록
        cursor.execute("""
            SELECT AD.ADMIT_ID, A.RECEPTION_ID, P.PATIENT_NAME, 
                   R.ROOM_NO, B.BED_NO, B.BED_ID,
                   TO_CHAR(AD.ADMIT_DT, 'YYYY-MM-DD HH24:MI') AS ADMIT_TIME,
                   AD.REASON, D.DOC_NAME
            FROM ADMISSIONS AD
            JOIN BEDS B ON AD.BED_ID = B.BED_ID
            JOIN ROOMS R ON B.ROOM_ID = R.ROOM_ID
            JOIN MEDICAL_RECORDS MR ON AD.RECORD_ID = MR.RECORD_ID
            JOIN APPOINTMENTS A ON MR.RECEPTION_ID = A.RECEPTION_ID
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            WHERE AD.DISCHARGE_DT IS NULL
            ORDER BY AD.ADMIT_DT DESC
        """)
        cols = [c[0] for c in cursor.description]
        admitted_patients = [dict(zip(cols, row)) for row in cursor.fetchall()]

        # 4. 사용 가능한 빈 병상 목록 (입원 모달 셀렉트박스용)
        cursor.execute("""
            SELECT B.BED_ID, R.ROOM_NO, B.BED_NO 
            FROM BEDS B 
            JOIN ROOMS R ON B.ROOM_ID = R.ROOM_ID 
            WHERE B.STATUS = '사용가능' OR B.STATUS IS NULL
            ORDER BY R.ROOM_NO, B.BED_NO
        """)
        cols = [c[0] for c in cursor.description]
        empty_beds = [dict(zip(cols, row)) for row in cursor.fetchall()]

        return render_template('admission/list.html', 
                               rooms=rooms, 
                               pending_patients=pending_patients, 
                               admitted_patients=admitted_patients,
                               empty_beds=empty_beds)
    finally:
        cursor.close()
        conn.close()

@admission_bp.route('/admit', methods=['POST'])
def process_admission():
    """입원 수속 처리: 병상 배정 및 상태 변경"""
    reception_id = request.form.get('reception_id')
    record_id = request.form.get('record_id')
    bed_id = request.form.get('bed_id')
    reason = request.form.get('reason')
    
    admit_id = 'ADM' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. ADMISSIONS 테이블에 신규 입원 데이터 삽입
        cursor.execute("""
            INSERT INTO ADMISSIONS (ADMIT_ID, RECORD_ID, BED_ID, ADMIT_DT, REASON)
            VALUES (:1, :2, :3, SYSDATE, :4)
        """, (admit_id, record_id, bed_id, reason))
        
        # 2. BEDS 상태를 '사용중'으로 변경
        cursor.execute("UPDATE BEDS SET STATUS = '사용중' WHERE BED_ID = :1", (bed_id,))
        
        # 3. 환자 예약 상태를 '입원중'으로 변경
        cursor.execute("UPDATE APPOINTMENTS SET STATUS = '입원중' WHERE RECEPTION_ID = :1", (reception_id,))
        
        conn.commit()
        flash("입원 수속 및 병상 배정이 성공적으로 완료되었습니다.", "success")
    except Exception as e:
        flash(f"입원 처리 중 오류 발생: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('admission.admission_list'))

@admission_bp.route('/discharge', methods=['POST'])
def process_discharge():
    """퇴원 수속 처리: 병상 반환 및 퇴원 수납 대기열로 이관"""
    admit_id = request.form.get('admit_id')
    bed_id = request.form.get('bed_id')
    reception_id = request.form.get('reception_id')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. ADMISSIONS 퇴원 일시 업데이트
        cursor.execute("UPDATE ADMISSIONS SET DISCHARGE_DT = SYSDATE WHERE ADMIT_ID = :1", (admit_id,))
        
        # 2. BEDS 상태를 '사용가능'으로 원복
        cursor.execute("UPDATE BEDS SET STATUS = '사용가능' WHERE BED_ID = :1", (bed_id,))
        
        # 3. 환자 상태를 '퇴원수납대기'로 변경 (수납 창구로 전달)
        cursor.execute("UPDATE APPOINTMENTS SET STATUS = '퇴원수납대기' WHERE RECEPTION_ID = :1", (reception_id,))
        
        conn.commit()
        flash("퇴원 처리가 완료되었습니다. (환자가 퇴원 수납 대기열로 이관되었습니다)", "success")
    except Exception as e:
        flash(f"퇴원 처리 중 오류 발생: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('admission.admission_list'))