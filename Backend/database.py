from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base
from pathlib import Path

# 获取当前文件所在目录（Backend/）
BACKEND_DIR = Path(__file__).parent.resolve()
# 项目根目录 = BACKEND_DIR 的父目录
PROJECT_ROOT = BACKEND_DIR
# 数据库路径：PROJECT_ROOT / Data / funds.db
DATABASE_PATH = PROJECT_ROOT / "Data" / "funds.db"

# 构造 SQLite URL
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def migrate_db():
    """数据库迁移：为现有表添加缺失的列"""
    with engine.connect() as conn:
        # 检查并添加 fund_watchlist.group_id 列
        try:
            result = conn.execute(text("PRAGMA table_info(fund_watchlist)"))
            columns = [row[1] for row in result.fetchall()]
            if 'group_id' not in columns:
                conn.execute(text("ALTER TABLE fund_watchlist ADD COLUMN group_id INTEGER DEFAULT NULL"))
                conn.commit()
                print("Migration: Added group_id column to fund_watchlist table")
        except Exception as e:
            print(f"Migration check for fund_watchlist: {e}")
        
        # 检查并添加 daily_market_summary 表的新列
        try:
            result = conn.execute(text("PRAGMA table_info(daily_market_summary)"))
            columns = [row[1] for row in result.fetchall()]
            if 'current_step' not in columns:
                conn.execute(text("ALTER TABLE daily_market_summary ADD COLUMN current_step INTEGER DEFAULT 0"))
                conn.commit()
                print("Migration: Added current_step column to daily_market_summary table")
            if 'step_message' not in columns:
                conn.execute(text("ALTER TABLE daily_market_summary ADD COLUMN step_message VARCHAR(200)"))
                conn.commit()
                print("Migration: Added step_message column to daily_market_summary table")
        except Exception as e:
            print(f"Migration check for daily_market_summary: {e}")

def init_db():
    # 确保 Data 目录存在
    (PROJECT_ROOT / "Data").mkdir(exist_ok=True)
    # 创建所有表（新表会被创建，已有表不会被覆盖）
    Base.metadata.create_all(bind=engine)
    # 执行数据库迁移
    migrate_db()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()