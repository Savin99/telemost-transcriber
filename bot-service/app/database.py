import os
from uuid import UUID

from sqlalchemy import Column, Float, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@postgres:5432/telemost_bot",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    meeting_url = Column(Text, nullable=False)
    bot_name = Column(Text, nullable=False, server_default="Транскрибатор")
    status = Column(String(50), nullable=False, server_default="joining")
    recording_path = Column(Text)
    duration_seconds = Column(Float)
    error_message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class TranscriptSegmentDB(Base):
    __tablename__ = "transcript_segments"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    meeting_id = Column(PG_UUID(as_uuid=True), nullable=False)
    speaker = Column(Text)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def update_meeting_status(
    session: AsyncSession,
    meeting_id: UUID,
    status: str,
    **kwargs,
):
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.status = status
        for key, value in kwargs.items():
            setattr(meeting, key, value)
        await session.commit()
