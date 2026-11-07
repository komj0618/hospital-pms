from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

lab_bp = Blueprint('lab', __name__)

@lab_bp.route('/')
def test_list():
    """검사 대기열 조회[cite: 9]"""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT TR.TEST_ID, TI.TEST_NAME, P.PATIENT_NAME, D.DOC_NAME, 
                   TO_CHAR(TR.RSV_DT, 'HH24:MI') AS RSV_TIME, TR.STATUS,
                   TR.RECORD_ID, MR.RECEPTION_ID
            FROM TEST_RECORDS TR
            JOIN TEST_ITEMS TI ON TR.TEST_CODE = TI.TEST_CODE
            JOIN MEDICAL_RECORDS MR ON TR.RECORD_ID = MR.RECORD_ID
            JOIN APPOINTMENTS A ON MR.RECEPTION_ID = A.RECEPTION_ID
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            WHERE TR.STATUS = '검사대기'
            ORDER BY TR.RSV_DT ASC
        """)
        columns = [col[0] for col in cursor.description]
        tests = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        return render_template('lab/list.html', tests=tests)
    finally:
        cursor.close()
        conn.close()

@lab_bp.route('/save_result', methods=['POST'])
def save_result():
    """검사 결과 입력 및 상태 변경[cite: 9]"""
    test_id = request.form.get('test_id')
    record_id = request.form.get('record_id')
    reception_id = request.form.get('reception_id')
    result_val = request.form.get('result_val')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 결과치 입력 및 실행일시 업데이트[cite: 9]
        cursor.execute("""
            UPDATE TEST_RECORDS 
            SET RESULT_VAL = :1, EXEC_DT = SYSDATE, STATUS = '검사완료'
            WHERE TEST_ID = :2
        """, (result_val, test_id))
        
        # 환자의 다른 남은 검사가 있는지 카운트
        cursor.execute("SELECT COUNT(*) FROM TEST_RECORDS WHERE RECORD_ID = :1 AND STATUS = '검사대기'", (record_id,))
        pending_count = cursor.fetchone()[0]
        
        if pending_count == 0:
            # 모든 검사가 끝나면 환자를 다시 의사에게 돌려보냄
            cursor.execute("UPDATE APPOINTMENTS SET STATUS = '검사완료' WHERE RECEPTION_ID = :1", (reception_id,))
            flash("모든 검사가 완료되어 환자가 진료실 대기열로 복귀했습니다.", "success")
        else:
            flash(f"단일 검사 결과가 저장되었습니다. (해당 환자 잔여 검사: {pending_count}건)", "info")
            
        conn.commit()
    except Exception as e:
        flash(f"결과 등록 오류: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('lab.test_list'))