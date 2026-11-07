import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

clinic_bp = Blueprint('clinic', __name__)

@clinic_bp.route('/')
def patient_list():
    """의사별 대기열 및 환자 관리"""
    doctor_id = request.args.get('doctor_id', 'DOC01')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 의사 목록 (상단 필터용)
        cursor.execute("SELECT DOCTOR_ID, DOC_NAME FROM DOCTORS ORDER BY DOCTOR_ID")
        doctors = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
        
        # 2. 진료실 대기 환자 (대기중, 진료중, 검사완료 상태)
        cursor.execute("""
            SELECT A.RECEPTION_ID, P.PATIENT_NAME, P.GENDER, 
                   TO_CHAR(P.BIRTH_DATE, 'YYYY-MM-DD') AS BIRTH_DATE,
                   A.STATUS, TO_CHAR(A.RECEPT_DT, 'HH24:MI') AS RECEPT_TIME,
                   M.RECORD_ID, M.SYMPTOM
            FROM APPOINTMENTS A
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            LEFT JOIN MEDICAL_RECORDS M ON A.RECEPTION_ID = M.RECEPTION_ID
            WHERE A.DOCTOR_ID = :1 AND A.STATUS IN ('대기중', '진료중', '검사완료')
            ORDER BY 
                CASE A.STATUS WHEN '진료중' THEN 1 WHEN '검사완료' THEN 2 ELSE 3 END, 
                A.RECEPT_DT ASC
        """, (doctor_id,))
        patients = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]

        # 3. 환자별 검사 결과 조회 매핑
        for p in patients:
            if p.get('RECORD_ID'):
                cursor.execute("""
                    SELECT TI.TEST_NAME, 
                           TO_CHAR(TR.EXEC_DT, 'YYYY-MM-DD HH24:MI') AS EXEC_DT, 
                           TR.STATUS, 
                           TR.RESULT_VAL
                    FROM TEST_RECORDS TR
                    JOIN TEST_ITEMS TI ON TR.TEST_CODE = TI.TEST_CODE
                    WHERE TR.RECORD_ID = :1
                    ORDER BY TR.RSV_DT ASC
                """, (p['RECORD_ID'],))
                t_cols = [c[0] for c in cursor.description]
                p['test_results'] = [dict(zip(t_cols, row)) for row in cursor.fetchall()]
            else:
                p['test_results'] = []

        # 4. 검사 항목 및 약품 기초 데이터
        cursor.execute("SELECT TEST_CODE, TEST_NAME FROM TEST_ITEMS ORDER BY TEST_CODE")
        test_items = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
        
        cursor.execute("SELECT MED_ID, MED_NAME FROM MEDICINES ORDER BY MED_NAME")
        medicines = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
        
        return render_template('clinic/list.html', doctor_id=doctor_id, doctors=doctors, 
                               patients=patients, test_items=test_items, medicines=medicines)
    finally:
        cursor.close()
        conn.close()

@clinic_bp.route('/start_treatment', methods=['POST'])
def start_treatment():
    """진료 시작 (상태를 '진료중'으로 변경)"""
    reception_id = request.form.get('reception_id')
    doctor_id = request.form.get('doctor_id')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE APPOINTMENTS SET STATUS = '진료중' WHERE RECEPTION_ID = :1", (reception_id,))
        conn.commit()
    except Exception as e:
        flash(f"상태 변경 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('clinic.patient_list', doctor_id=doctor_id))

@clinic_bp.route('/save_treatment', methods=['POST'])
def save_treatment():
    """진료 저장: 검사 오더 또는 최종 진료 완료 분기 처리"""
    action = request.form.get('action') # 'order_test' 또는 'complete'
    reception_id = request.form.get('reception_id')
    record_id = request.form.get('record_id')
    doctor_id = request.form.get('doctor_id')
    symptom = request.form.get('symptom')
    diagnosis = request.form.get('diagnosis')
    test_codes = request.form.getlist('test_codes')
    
    # 약품 처방 리스트
    med_ids = request.form.getlist('med_id[]')
    dose_amounts = request.form.getlist('dose_amount[]')
    daily_counts = request.form.getlist('daily_count[]')
    total_days = request.form.getlist('total_days[]')

    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 기존 RECORD_ID가 없으면 신규 발급
        if not record_id:
            record_id = 'REC' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            cursor.execute("""
                INSERT INTO MEDICAL_RECORDS (RECORD_ID, RECEPTION_ID, TREATMENT_DT, SYMPTOM, DIAGNOSIS)
                VALUES (:1, :2, SYSDATE, :3, :4)
            """, (record_id, reception_id, symptom, diagnosis if action == 'complete' else None))
        else:
            cursor.execute("""
                UPDATE MEDICAL_RECORDS 
                SET SYMPTOM = :1, DIAGNOSIS = :2 
                WHERE RECORD_ID = :3
            """, (symptom, diagnosis, record_id))

        if action == 'order_test':
            # 1. 검사 오더 처리 -> 검사실 이관
            if not test_codes:
                flash("선택된 검사 항목이 없습니다. 검사를 체크해 주세요.", "error")
                return redirect(url_for('clinic.patient_list', doctor_id=doctor_id))
            
            for idx, t_code in enumerate(test_codes):
                test_id = 'T' + datetime.datetime.now().strftime('%Y%m%d%H%M%S') + str(idx).zfill(2)
                cursor.execute("""
                    INSERT INTO TEST_RECORDS (TEST_ID, RECORD_ID, TEST_CODE, RSV_DT, STATUS)
                    VALUES (:1, :2, :3, SYSDATE, '검사대기')
                """, (test_id, record_id, t_code))
                
            cursor.execute("UPDATE APPOINTMENTS SET STATUS = '검사대기' WHERE RECEPTION_ID = :1", (reception_id,))
            flash("검사 오더가 발행되어 환자가 검사실로 이동했습니다.", "success")

        elif action == 'complete':
            # 2. 최종 진료 완료 처리 -> 수납 이관
            for i in range(len(med_ids)):
                if med_ids[i] and dose_amounts[i] and daily_counts[i] and total_days[i]:
                    dose = max(0.1, float(dose_amounts[i]))
                    daily = max(1, int(daily_counts[i]))
                    days = max(1, int(total_days[i]))
                    pres_id = 'P' + datetime.datetime.now().strftime('%Y%m%d%H%M%S') + str(i).zfill(2)
                    cursor.execute("""
                        INSERT INTO PRESCRIPTIONS (PRES_ID, RECORD_ID, MED_ID, DOSE_AMOUNT, DAILY_COUNT, TOTAL_DAYS)
                        VALUES (:1, :2, :3, :4, :5, :6)
                    """, (pres_id, record_id, med_ids[i], dose, daily, days))
                    
            cursor.execute("UPDATE APPOINTMENTS SET STATUS = '진료완료' WHERE RECEPTION_ID = :1", (reception_id,))
            flash("진료 및 처방이 완료되어 환자가 수납 대기열로 이동했습니다.", "success")
            
        conn.commit()
    except Exception as e:
        flash(f"진료 처리 중 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('clinic.patient_list', doctor_id=doctor_id))