import os
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Float, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///transcriber.db",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _generate_uuid():
    return str(_uuid.uuid4())


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(String(36), primary_key=True, default=_generate_uuid)
    meeting_url = Column(Text, nullable=False)
    bot_name = Column(Text, nullable=False, default="Транскрибатор")
    status = Column(String(50), nullable=False, default="joining")
    recording_path = Column(Text)
    duration_seconds = Column(Float)
    error_message = Column(Text)
    created_at = Column(Text, default=_now_iso)
    updated_at = Column(Text, default=_now_iso, onupdate=_now_iso)


class TranscriptSegmentDB(Base):
    __tablename__ = "transcript_segments"

    id = Column(String(36), primary_key=True, default=_generate_uuid)
    meeting_id = Column(String(36), nullable=False)
    speaker = Column(Text)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)


async def init_db():
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session():
    async with async_session() as session:
        yield session


async def update_meeting_status(
    session: AsyncSession,
    meeting_id,
    status: str,
    **kwargs,
):
    meeting_id_str = str(meeting_id)
    meeting = await session.get(Meeting, meeting_id_str)
    if meeting:
        meeting.status = status
        meeting.updated_at = _now_iso()
        for key, value in kwargs.items():
            setattr(meeting, key, value)
        await session.commit()
