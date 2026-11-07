import os
from flask import Flask, render_template
from dotenv import load_dotenv

# .env 로드
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback-key")

# Blueprint 모듈 등록 (views 폴더)
from views.reception import reception_bp
from views.clinic import clinic_bp
from views.admission import admission_bp
from views.payment import payment_bp
from views.lab import lab_bp

app.register_blueprint(reception_bp, url_prefix='/reception')
app.register_blueprint(clinic_bp, url_prefix='/clinic')
app.register_blueprint(admission_bp, url_prefix='/admission')
app.register_blueprint(payment_bp, url_prefix='/payment')
app.register_blueprint(lab_bp, url_prefix='/lab')

@app.route('/')
def index():
    """메인 대시보드 홈 화면"""
    return render_template('base.html')

if __name__ == '__main__':
    # 로컬 개발 서버 실행 (디버그 모드)
    app.run(host='0.0.0.0', port=5000, debug=True)