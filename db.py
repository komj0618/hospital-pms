import os
import oracledb
from dotenv import load_dotenv

# .env 파일에서 환경변수 로드
load_dotenv()

# NLS_LANG 설정 (한글 인코딩 방지)
os.environ['NLS_LANG'] = 'AMERICAN_AMERICA.AL32UTF8'

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")

# 커넥션 풀 생성 (Thin 모드 기본 동작)
pool = oracledb.create_pool(
    user=DB_USER,
    password=DB_PASSWORD,
    dsn=DB_DSN,
    min=2,
    max=10,
    increment=1
)

def get_db_connection():
    """
    커넥션 풀에서 DB 연결 객체를 꺼내 반환합니다.
    사용 완료 후 반드시 conn.close()를 호출해야 합니다.
    """
    return pool.acquire()