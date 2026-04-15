CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE meetings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_url TEXT NOT NULL,
    bot_name TEXT NOT NULL DEFAULT 'Транскрибатор',
    status TEXT NOT NULL DEFAULT 'pending',
    recording_path TEXT,
    duration_seconds FLOAT,
    error_message TEXT,
    transcript_url TEXT,
    drive_file_id TEXT,
    drive_folder_id TEXT,
    drive_filename TEXT,
    drive_web_view_link TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE transcript_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker TEXT,
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX idx_segments_meeting_id ON transcript_segments(meeting_id);
CREATE INDEX idx_meetings_status ON meetings(status);
