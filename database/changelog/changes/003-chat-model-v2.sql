--liquibase formatted sql

--changeset conductor:003-chat-rooms-enhance
--comment: Add room name, mode, workspace info to chat_rooms
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS name VARCHAR;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS mode VARCHAR NOT NULL DEFAULT 'local';
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS workspace_path VARCHAR;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS repo_url VARCHAR;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS branch VARCHAR;
COMMENT ON COLUMN chat_rooms.name IS 'Human-readable room name (set by user or AI on first query)';
COMMENT ON COLUMN chat_rooms.mode IS 'local or online';
COMMENT ON COLUMN chat_rooms.workspace_path IS 'Local workspace path (local mode) or worktree path (online mode)';
COMMENT ON COLUMN chat_rooms.repo_url IS 'Git repo URL (online mode)';
COMMENT ON COLUMN chat_rooms.branch IS 'Git branch name';
--rollback ALTER TABLE chat_rooms DROP COLUMN IF EXISTS name; ALTER TABLE chat_rooms DROP COLUMN IF EXISTS mode; ALTER TABLE chat_rooms DROP COLUMN IF EXISTS workspace_path; ALTER TABLE chat_rooms DROP COLUMN IF EXISTS repo_url; ALTER TABLE chat_rooms DROP COLUMN IF EXISTS branch;

--changeset conductor:003-chat-room-participants
--comment: Normalized participant tracking per room
CREATE TABLE IF NOT EXISTS chat_room_participants (
    id              SERIAL           NOT NULL PRIMARY KEY,
    room_id         VARCHAR          NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    user_id         VARCHAR          NOT NULL,
    display_name    VARCHAR          NOT NULL DEFAULT '',
    role            VARCHAR          NOT NULL DEFAULT 'guest',
    identity_source VARCHAR          NOT NULL DEFAULT 'anonymous',
    email           VARCHAR,
    provider        VARCHAR,
    joined_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    left_at         TIMESTAMPTZ,
    is_active       BOOLEAN          NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS ix_chat_participants_room ON chat_room_participants (room_id);
CREATE INDEX IF NOT EXISTS ix_chat_participants_email ON chat_room_participants (email);
CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_participants_room_user ON chat_room_participants (room_id, user_id);
--rollback DROP TABLE IF EXISTS chat_room_participants;

--changeset conductor:003-chat-messages-enhance
--comment: Add identity_source, metadata, and parent_message_id to chat_messages
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS identity_source VARCHAR NOT NULL DEFAULT 'anonymous';
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS parent_message_id VARCHAR;
COMMENT ON COLUMN chat_messages.identity_source IS 'How sender identity was established: sso, named, anonymous, ai';
COMMENT ON COLUMN chat_messages.metadata IS 'JSON — structured data for code_snippet (file_path, language, start_line), file (file_id), etc.';
COMMENT ON COLUMN chat_messages.parent_message_id IS 'ID of parent message for thread/reply chains (e.g. AI answer → user question → code_snippet)';
CREATE INDEX IF NOT EXISTS ix_chat_msg_parent ON chat_messages (parent_message_id) WHERE parent_message_id IS NOT NULL;
--rollback ALTER TABLE chat_messages DROP COLUMN IF EXISTS identity_source; ALTER TABLE chat_messages DROP COLUMN IF EXISTS metadata; ALTER TABLE chat_messages DROP COLUMN IF EXISTS parent_message_id;
