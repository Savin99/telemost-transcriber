import os
import uuid as _uuid
from datetime import datetime, timezone
import logging

from sqlalchemy import Column, Float, String, Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

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
    status = Column(String(50), nullable=False, default="pending")
    recording_path = Column(Text)
    duration_seconds = Column(Float)
    error_message = Column(Text)
    transcript_url = Column(Text)
    drive_file_id = Column(Text)
    drive_folder_id = Column(Text)
    drive_filename = Column(Text)
    drive_web_view_link = Column(Text)
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


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _migrate_postgres_transcript_segment_id(conn):
    """Migrate legacy transcript_segments.id INTEGER -> UUID.

    The migration is idempotent and runs only on PostgreSQL.
    """
    if conn.dialect.name != "postgresql":
        return

    table_exists = await conn.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'transcript_segments'
            )
            """
        )
    )
    if not table_exists:
        return

    id_type = await conn.scalar(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'transcript_segments'
              AND column_name = 'id'
            """
        )
    )

    if id_type not in {"smallint", "integer", "bigint"}:
        return

    logger.warning(
        "Legacy transcript_segments.id type '%s' detected, migrating to UUID",
        id_type,
    )

    await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))

    await conn.execute(
        text(
            """
            ALTER TABLE transcript_segments
            ADD COLUMN IF NOT EXISTS id_uuid_tmp UUID
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE transcript_segments
            SET id_uuid_tmp = gen_random_uuid()
            WHERE id_uuid_tmp IS NULL
            """
        )
    )

    pk_name = await conn.scalar(
        text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints AS tc
            WHERE tc.table_schema = current_schema()
              AND tc.table_name = 'transcript_segments'
              AND tc.constraint_type = 'PRIMARY KEY'
            LIMIT 1
            """
        )
    )
    if pk_name:
        await conn.exec_driver_sql(
            f"ALTER TABLE transcript_segments DROP CONSTRAINT {_quote_ident(pk_name)}"
        )

    await conn.execute(text("ALTER TABLE transcript_segments DROP COLUMN id"))
    await conn.execute(text("ALTER TABLE transcript_segments RENAME COLUMN id_uuid_tmp TO id"))
    await conn.execute(text("ALTER TABLE transcript_segments ALTER COLUMN id SET NOT NULL"))
    await conn.execute(text("ALTER TABLE transcript_segments ADD PRIMARY KEY (id)"))

    logger.info("Migration complete: transcript_segments.id is now UUID")


async def _ensure_meeting_upload_columns(conn):
    def _get_columns(sync_conn):
        return {column["name"] for column in inspect(sync_conn).get_columns("meetings")}

    column_names = await conn.run_sync(_get_columns)
    required_columns = {
        "transcript_url": "TEXT",
        "drive_file_id": "TEXT",
        "drive_folder_id": "TEXT",
        "drive_filename": "TEXT",
        "drive_web_view_link": "TEXT",
    }

    for column_name, sql_type in required_columns.items():
        if column_name in column_names:
            continue
        await conn.execute(
            text(f"ALTER TABLE meetings ADD COLUMN {column_name} {sql_type}")
        )
        logger.info("Added meetings.%s column", column_name)


async def init_db():
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        await _migrate_postgres_transcript_segment_id(conn)
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_meeting_upload_columns(conn)


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
