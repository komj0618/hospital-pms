import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

reception_bp = Blueprint('reception', __name__)

@reception_bp.route('/')
def patient_list():
    """환자 목록 및 당일 대기열 분리 조회"""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 현재 대기 중인 환자 목록 (DOCTORS 테이블 조인하여 의사 이름 DOC_NAME 추출)
        cursor.execute("""
            SELECT A.RECEPTION_ID, 
                   P.PATIENT_NAME, 
                   P.GENDER, 
                   TO_CHAR(A.RECEPT_DT, 'HH24:MI') AS RECEPT_TIME,
                   D.DOC_NAME, 
                   A.STATUS
            FROM APPOINTMENTS A
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            LEFT JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            WHERE A.STATUS = '대기중'
            ORDER BY A.RECEPT_DT ASC
        """)
        columns_waiting = [col[0] for col in cursor.description]
        waiting_list = [dict(zip(columns_waiting, row)) for row in cursor.fetchall()]

        # 2. 전체 환자 목록 및 최근 예약/접수 상태 조회
        cursor.execute("""
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
            ORDER BY P.PATIENT_ID DESC
        """)
        columns_patients = [col[0] for col in cursor.description]
        patients = [dict(zip(columns_patients, row)) for row in cursor.fetchall()]
        
        return render_template('reception/list.html', patients=patients, waiting_list=waiting_list)
    finally:
        cursor.close()
        conn.close()

@reception_bp.route('/add_patient', methods=['POST'])
def add_patient():
    """신규 환자 등록"""
    patient_id = request.form.get('patient_id')
    patient_name = request.form.get('patient_name')
    birth_date = request.form.get('birth_date')
    phone = request.form.get('phone')
    gender = request.form.get('gender')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO PATIENTS (PATIENT_ID, PATIENT_NAME, BIRTH_DATE, PHONE, GENDER)
            VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)
        """, (patient_id, patient_name, birth_date, phone, gender))
        conn.commit()
        flash(f"{patient_name} 환자가 등록되었습니다.", "success")
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
        flash(f"당일 대기열에 추가되었습니다.", "success")
    except Exception as e:
        flash(f"대기열 추가 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/add_appointment', methods=['POST'])
def add_appointment():
    """미래 일정 예약 (대기열에는 안 들어감)"""
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
        flash(f"진료 일정이 예약되었습니다.", "success")
    except Exception as e:
        flash(f"일정 예약 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/checkin', methods=['POST'])
def checkin():
    """예약 환자의 당일 내원 (상태를 '대기중'으로 UPDATE)"""
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
        flash(f"예약 환자의 내원 처리가 완료되어 대기열에 추가되었습니다.", "success")
    except Exception as e:
        flash(f"내원 처리 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('reception.patient_list'))

@reception_bp.route('/delete_queue', methods=['POST'])
def delete_queue():
    """잘못 접수된 대기열 또는 예약 취소 (DB 테이블에서 DELETE)"""
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