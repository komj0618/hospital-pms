import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

reception_bp = Blueprint('reception', __name__)

@reception_bp.route('/')
def patient_list():
    """환자 목록 및 당일 대기열/진행상황 모니터링 (검색 기능 지원)"""
    search_keyword = request.args.get('search', '').strip()

    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 당일 활성 환자 모니터링 (대기중, 진료중, 검사대기, 검사완료)
        cursor.execute("""
            SELECT A.RECEPTION_ID, 
                   P.PATIENT_NAME, 
                   P.GENDER, 
                   TO_CHAR(NVL(A.RECEPT_DT, A.APPT_DT), 'HH24:MI') AS RECEPT_TIME,
                   NVL(D.DOC_NAME, '미배정') AS DOC_NAME, 
                   A.STATUS
            FROM APPOINTMENTS A
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            LEFT JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            WHERE A.STATUS IN ('대기중', '진료중', '검사대기', '검사완료')
            ORDER BY 
                CASE A.STATUS 
                    WHEN '진료중' THEN 1 
                    WHEN '대기중' THEN 2 
                    WHEN '검사완료' THEN 3 
                    WHEN '검사대기' THEN 4 
                    ELSE 5 
                END, 
                A.RECEPT_DT ASC
        """)
        columns_waiting = [col[0] for col in cursor.description]
        waiting_list = [dict(zip(columns_waiting, row)) for row in cursor.fetchall()]

        # 2. 전체 환자 목록 및 최근 접수/진행 상태 조회 (검색어 필터 적용)
        query = """
            SELECT P.PATIENT_ID, P.PATIENT_NAME, 
                   TO_CHAR(P.BIRTH_DATE, 'YYYY-MM-DD') AS BIRTH_DATE, 
                   P.PHONE, P.GENDER,
                   A.RECEPTION_ID,
                   TO_CHAR(A.APPT_DT, 'YYYY-MM-DD HH24:MI') AS APPT_DT,
                   A.STATUS
            FROM PATIENTS P
            LEFT JOIN (
                SELECT RECEPTION_ID, PATIENT_ID, APPT_DT, STATUS,
                       ROW_NUMBER() OVER (PARTITION BY PATIENT_ID ORDER BY RECEPTION_ID DESC) as RN
                FROM APPOINTMENTS
            ) A ON P.PATIENT_ID = A.PATIENT_ID AND A.RN = 1
        """
        params = []
        if search_keyword:
            query += """
                WHERE UPPER(P.PATIENT_ID) LIKE UPPER(:1)
                   OR P.PATIENT_NAME LIKE :2
                   OR P.PHONE LIKE :3
            """
            wildcard = f"%{search_keyword}%"
            params = [wildcard, wildcard, wildcard]

        query += " ORDER BY P.PATIENT_ID DESC"
        cursor.execute(query, params)
        
        columns_patients = [col[0] for col in cursor.description]
        patients = [dict(zip(columns_patients, row)) for row in cursor.fetchall()]
        
        return render_template('reception/list.html', 
                               patients=patients, 
                               waiting_list=waiting_list, 
                               search_keyword=search_keyword)
    finally:
        cursor.close()
        conn.close()

@reception_bp.route('/add_patient', methods=['POST'])
def add_patient():
    """신규 환자 등록 (현재 연도 2자리 + 4자리 순번 자동 채번: P260001 -> 다음 해 P270001)"""
    patient_name = request.form.get('patient_name')
    birth_date = request.form.get('birth_date')
    phone = request.form.get('phone')
    gender = request.form.get('gender')
    
    # 1. 현재 연도 2자리 추출 (예: 2026년 -> '26', 2027년 -> '27')
    current_year_prefix = 'P' + datetime.datetime.now().strftime('%y')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 2. 해당 연도 접두어(예: P26%, P27%)로 시작하는 최댓값을 찾아 +1 계산
        cursor.execute("""
            SELECT NVL(MAX(TO_NUMBER(SUBSTR(PATIENT_ID, 4))), 0) + 1 
            FROM PATIENTS 
            WHERE PATIENT_ID LIKE :1
        """, (current_year_prefix + '%',))
        
        next_seq = cursor.fetchone()[0]
        new_patient_id = f"{current_year_prefix}{int(next_seq):04d}"

        # 3. 신규 환자 삽입
        cursor.execute("""
            INSERT INTO PATIENTS (PATIENT_ID, PATIENT_NAME, BIRTH_DATE, PHONE, GENDER)
            VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)
        """, (new_patient_id, patient_name, birth_date, phone, gender))
        conn.commit()
        flash(f"신규 환자 [{patient_name}] 님이 성공적으로 등록되었습니다. (환자번호: {new_patient_id})", "success")
    except Exception as e:
        flash(f"환자 등록 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/add_queue', methods=['POST'])
def add_queue():
    """예약 없는 당일 현장 방문 (대기열 바로 추가)"""
    patient_id = request.form.get('patient_id')
    doctor_id = request.form.get('doctor_id')
    reception_id = 'R' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO APPOINTMENTS (RECEPTION_ID, PATIENT_ID, DOCTOR_ID, RECEPT_DT, APPT_DT, STATUS, APPT_TYPE)
            VALUES (:1, :2, :3, SYSDATE, NULL, '대기중', '당일접수')
        """, (reception_id, patient_id, doctor_id))
        conn.commit()
        flash("당일 대기열에 추가되었습니다.", "success")
    except Exception as e:
        flash(f"대기열 추가 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/add_appointment', methods=['POST'])
def add_appointment():
    """미래 일정 예약"""
    patient_id = request.form.get('patient_id')
    doctor_id = request.form.get('doctor_id')
    appt_dt = request.form.get('appt_dt')
    reception_id = 'R' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO APPOINTMENTS (RECEPTION_ID, PATIENT_ID, DOCTOR_ID, RECEPT_DT, APPT_DT, STATUS, APPT_TYPE)
            VALUES (:1, :2, :3, NULL, TO_DATE(:4, 'YYYY-MM-DD"T"HH24:MI'), '예약완료', '일반진료')
        """, (reception_id, patient_id, doctor_id, appt_dt))
        conn.commit()
        flash("진료 일정이 예약되었습니다.", "success")
    except Exception as e:
        flash(f"일정 예약 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/checkin', methods=['POST'])
def checkin():
    """예약 환자의 당일 내원 확인 (대기열 전환)"""
    reception_id = request.form.get('reception_id')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE APPOINTMENTS 
            SET STATUS = '대기중', RECEPT_DT = SYSDATE 
            WHERE RECEPTION_ID = :1
        """, (reception_id,))
        conn.commit()
        flash("예약 환자의 내원 처리가 완료되어 대기열에 추가되었습니다.", "success")
    except Exception as e:
        flash(f"내원 처리 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/delete_queue', methods=['POST'])
def delete_queue():
    """대기열 또는 예약 취소"""
    reception_id = request.form.get('reception_id')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM APPOINTMENTS WHERE RECEPTION_ID = :1", (reception_id,))
        conn.commit()
        flash("해당 접수/예약 내역이 완전히 삭제되었습니다.", "success")
    except Exception as e:
        flash(f"삭제 처리 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/edit_patient', methods=['POST'])
def edit_patient():
    """환자 인적사항 수정 (이름, 생년월일, 연락처, 성별)"""
    patient_id = request.form.get('patient_id')
    patient_name = request.form.get('patient_name')
    birth_date = request.form.get('birth_date')
    phone = request.form.get('phone')
    gender = request.form.get('gender')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE PATIENTS
            SET PATIENT_NAME = :1,
                BIRTH_DATE = TO_DATE(:2, 'YYYY-MM-DD'),
                PHONE = :3,
                GENDER = :4
            WHERE PATIENT_ID = :5
        """, (patient_name, birth_date, phone, gender, patient_id))
        conn.commit()
        flash(f"[{patient_id}] {patient_name} 환자의 정보가 성공적으로 수정되었습니다.", "success")
    except Exception as e:
        flash(f"환자 정보 수정 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))