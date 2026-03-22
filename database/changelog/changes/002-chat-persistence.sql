--liquibase formatted sql

--changeset conductor:002-chat-rooms
--comment: Room metadata — lifecycle independent of in-memory state
CREATE TABLE IF NOT EXISTS chat_rooms (
    id              VARCHAR        NOT NULL PRIMARY KEY,
    owner_email     VARCHAR,
    owner_provider  VARCHAR,
    display_name    VARCHAR,
    status          VARCHAR        NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_chat_rooms_owner ON chat_rooms (owner_email);
CREATE INDEX IF NOT EXISTS ix_chat_rooms_status ON chat_rooms (status);
--rollback DROP TABLE IF EXISTS chat_rooms;

--changeset conductor:002-chat-messages
--comment: Durable message archive — mirrors ChatMessage Pydantic model
CREATE TABLE IF NOT EXISTS chat_messages (
    id              VARCHAR          NOT NULL PRIMARY KEY,
    room_id         VARCHAR          NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    user_id         VARCHAR          NOT NULL,
    display_name    VARCHAR          NOT NULL DEFAULT '',
    role            VARCHAR          NOT NULL,
    type            VARCHAR          NOT NULL DEFAULT 'message',
    content         TEXT             NOT NULL,
    ai_data         TEXT,
    ts              DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_chat_msg_room_ts ON chat_messages (room_id, ts);
--rollback DROP TABLE IF EXISTS chat_messages;
