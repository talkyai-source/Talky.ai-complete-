"""
Database Connection and Session Management
Connects to Supabase PostgreSQL
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from contextlib import contextmanager
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database URL directly from environment
# For Supabase, get this from: Settings > Database > Connection String > URI
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables. Check your .env file")

# Create engine
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,  # Disable connection pooling for serverless
    echo=False,  # Set to True for debugging SQL queries
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db() -> Session:
    """
    Get database session with automatic cleanup
    
    Usage:
        with get_db() as db:
            campaigns = db.query(Campaign).all()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session() -> Session:
    """
    Get database session for FastAPI dependency injection
    
    Usage:
        @app.get("/campaigns")
        def list_campaigns(db: Session = Depends(get_db_session)):
            return db.query(Campaign).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
