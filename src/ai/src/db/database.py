import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# speako-ai-server/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'speako.db')}"

# check_same_thread=False: FastAPI가 요청을 여러 스레드에서 처리하므로 커넥션 공유를 허용.
# timeout=15: 다른 커넥션이 쓰기 락을 잡고 있을 때 즉시 'database is locked'로 죽지 않고 15초 대기.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 15})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    # WAL 모드는 읽기와 쓰기가 서로 블로킹하지 않게 해 동시성 하에서 'database is locked' 발생을 크게 줄인다.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from db import models  # noqa: F401 (모델을 import해야 create_all이 테이블을 인식함)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# create_all은 "없는 테이블"만 만들고, 이미 있는 테이블에 컬럼이 추가돼도 손대지 않는다.
# 그래서 모델에 컬럼을 새로 넣으면 기존 DB 파일에서는 그 컬럼이 없어 조회가 깨진다.
# 마이그레이션 도구(alembic)를 붙일 규모는 아니라, 빠진 컬럼만 ALTER로 채우는 최소 장치를 둔다.
_EXPECTED_COLUMNS = {
    "pronunciation_evaluations": {
        "feedback": "JSON", "reference_text": "TEXT", "recognized_text": "TEXT",
        "slide_number": "INTEGER",
    },
    "difficult_words": {
        "description": "TEXT",
    },
    "script_jobs": {
        "step": "INTEGER",
    },
}


def _add_missing_columns():
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table, columns in _EXPECTED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in present:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                    print(f"🛠️  DB 마이그레이션: {table}.{name} 컬럼을 추가했습니다.")
