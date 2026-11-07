import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
import db

payment_bp = Blueprint('payment', __name__)

@payment_bp.route('/')
def payment_list():
    """수납 대기 환자 목록 및 정산 내역 산출"""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 수납 대상 환자 조회 (외래 진료완료 및 병동 퇴원수납대기 환자)
        cursor.execute("""
            SELECT A.RECEPTION_ID, P.PATIENT_NAME, P.GENDER, 
                   TO_CHAR(P.BIRTH_DATE, 'YYYY-MM-DD') AS BIRTH_DATE,
                   A.STATUS AS APPT_STATUS,
                   D.DOC_NAME, MR.RECORD_ID,
                   TO_CHAR(MR.TREATMENT_DT, 'YYYY-MM-DD HH24:MI') AS TREAT_TIME,
                   MR.DIAGNOSIS
            FROM APPOINTMENTS A
            JOIN PATIENTS P ON A.PATIENT_ID = P.PATIENT_ID
            JOIN DOCTORS D ON A.DOCTOR_ID = D.DOCTOR_ID
            JOIN MEDICAL_RECORDS MR ON A.RECEPTION_ID = MR.RECEPTION_ID
            WHERE A.STATUS IN ('진료완료', '퇴원수납대기')
            ORDER BY MR.TREATMENT_DT ASC
        """)
        cols = [c[0] for c in cursor.description]
        waiting_bills = [dict(zip(cols, row)) for row in cursor.fetchall()]

        # 2. 각 환자별 항목별 금액 실시간 계산
        for b in waiting_bills:
            record_id = b['RECORD_ID']
            base_fee = 15000  # 기본 진찰료
            
            # 검사비 합산
            cursor.execute("""
                SELECT TI.TEST_NAME, NVL(TI.BASE_PRICE, 0) AS PRICE
                FROM TEST_RECORDS TR
                JOIN TEST_ITEMS TI ON TR.TEST_CODE = TI.TEST_CODE
                WHERE TR.RECORD_ID = :1
            """, (record_id,))
            tests = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
            test_total = sum(t['PRICE'] for t in tests)
            
            # 약제비 합산 (단가 * 1회투약량 * 1일투여횟수 * 총투약일수)
            cursor.execute("""
                SELECT M.MED_NAME, NVL(M.UNIT_PRICE, 0) AS UNIT_PRICE,
                       PR.DOSE_AMOUNT, PR.DAILY_COUNT, PR.TOTAL_DAYS,
                       (NVL(M.UNIT_PRICE, 0) * PR.DOSE_AMOUNT * PR.DAILY_COUNT * PR.TOTAL_DAYS) AS TOTAL_PRICE
                FROM PRESCRIPTIONS PR
                JOIN MEDICINES M ON PR.MED_ID = M.MED_ID
                WHERE PR.RECORD_ID = :1
            """, (record_id,))
            meds = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
            med_total = sum(int(m['TOTAL_PRICE']) for m in meds)

            # 입원 병실료 합산 (입원 기록이 있는 환자)
            cursor.execute("""
                SELECT R.ROOM_NO, NVL(R.DAILY_RATE, 0) AS DAILY_RATE,
                       TO_CHAR(AD.ADMIT_DT, 'YYYY-MM-DD') AS ADMIT_DATE,
                       TO_CHAR(NVL(AD.DISCHARGE_DT, SYSDATE), 'YYYY-MM-DD') AS DISCHARGE_DATE,
                       CEIL(NVL(AD.DISCHARGE_DT, SYSDATE) - AD.ADMIT_DT) AS DAYS
                FROM ADMISSIONS AD
                JOIN BEDS B ON AD.BED_ID = B.BED_ID
                JOIN ROOMS R ON B.ROOM_ID = R.ROOM_ID
                WHERE AD.RECORD_ID = :1
            """, (record_id,))
            admit_info = cursor.fetchone()
            
            room_total = 0
            room_desc = None
            if admit_info:
                r_no, d_rate, a_date, d_date, days = admit_info
                calc_days = max(1, int(days) if days else 1)
                room_total = int(d_rate) * calc_days
                room_desc = f"{r_no}호 ({calc_days}일 입원, 일일 {d_rate:,}원)"

            b['base_fee'] = base_fee
            b['test_list'] = tests
            b['test_total'] = test_total
            b['med_list'] = meds
            b['med_total'] = med_total
            b['room_desc'] = room_desc
            b['room_total'] = room_total
            b['grand_total'] = base_fee + test_total + med_total + room_total

        # 3. 최근 완료된 결제 이력 10건 조회
        cursor.execute("""
            SELECT P.PAY_ID, A.RECEPTION_ID, PT.PATIENT_NAME,
                   P.TOTAL_AMT, P.PAY_METHOD, P.STATUS,
                   TO_CHAR(P.PAY_DT, 'YYYY-MM-DD HH24:MI') AS PAY_TIME
            FROM PAYMENTS P
            JOIN APPOINTMENTS A ON P.RECEPTION_ID = A.RECEPTION_ID
            JOIN PATIENTS PT ON A.PATIENT_ID = PT.PATIENT_ID
            ORDER BY P.PAY_DT DESC
            FETCH FIRST 10 ROWS ONLY
        """)
        cols_hist = [c[0] for c in cursor.description]
        payment_history = [dict(zip(cols_hist, row)) for row in cursor.fetchall()]

        return render_template('payment/list.html', waiting_bills=waiting_bills, payment_history=payment_history)
    finally:
        cursor.close()
        conn.close()

@payment_bp.route('/pay', methods=['POST'])
def process_payment():
    """수납 승인 처리: PAYMENTS 테이블 INSERT 및 상태 종결"""
    reception_id = request.form.get('reception_id')
    total_amt = int(request.form.get('total_amt', 0))
    pay_method = request.form.get('pay_method', '신용카드')
    
    pay_id = 'PAY' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    conn = db.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. PAYMENTS 테이블에 결제 내역 저장
        cursor.execute("""
            INSERT INTO PAYMENTS (PAY_ID, RECEPTION_ID, TOTAL_AMT, PATIENT_AMT, PAID_AMT, STATUS, PAY_METHOD, PAY_DT)
            VALUES (:1, :2, :3, :4, :5, '결제완료', :6, SYSDATE)
        """, (pay_id, reception_id, total_amt, total_amt, total_amt, pay_method))
        
        # 2. APPOINTMENTS 상태를 '수납완료'로 변경하여 전 프로세스 종결
        cursor.execute("UPDATE APPOINTMENTS SET STATUS = '수납완료' WHERE RECEPTION_ID = :1", (reception_id,))
        
        conn.commit()
        flash(f"수납 번호 [{pay_id}] 결제가 완료되었습니다. ({pay_method} / {total_amt:,}원)", "success")
    except Exception as e:
        flash(f"수납 처리 중 오류 발생: {str(e)}", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('payment.payment_list'))