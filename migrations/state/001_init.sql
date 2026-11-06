-- Esquema de estado (sección 7.3 del diseño).
-- Fechas ISO-8601 en UTC; la purga por TTL la hace la capa de aplicación.

CREATE TABLE sessions (
    session_key     TEXT PRIMARY KEY,   -- tenant|conversation|user
    tenant_id       TEXT,
    conversation_id TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    app_hint        TEXT,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE TABLE messages (
    id           INTEGER PRIMARY KEY,
    session_key  TEXT NOT NULL REFERENCES sessions(session_key),
    role         TEXT NOT NULL,          -- user | assistant
    content      TEXT NOT NULL,
    sources      TEXT,                   -- JSON
    created_at   TEXT NOT NULL
);

CREATE TABLE activities (
    activity_id  TEXT PRIMARY KEY,
    response     TEXT NOT NULL,          -- JSON ya formateado
    created_at   TEXT NOT NULL
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    value      TEXT NOT NULL,
    comment    TEXT,
    created_at TEXT NOT NULL
);
