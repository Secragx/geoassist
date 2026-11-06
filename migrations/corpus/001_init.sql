-- Esquema del corpus (sección 7.2 del diseño).
-- Cada versión publicada es inmutable; la API lo abre en modo de solo lectura.

CREATE TABLE documents (
    doc_id         TEXT PRIMARY KEY,       -- hash estable de la ruta relativa
    rel_path       TEXT NOT NULL UNIQUE,  -- ruta POSIX relativa a /manuals
    title          TEXT NOT NULL,
    app            TEXT NOT NULL,          -- geopos | geoinventarios | geopricer | geopromotion | unknown
    status         TEXT NOT NULL,          -- approved | pending | archived
    version        TEXT,
    content_hash   TEXT NOT NULL,
    frontmatter    TEXT,                   -- JSON serializado
    chunk_count    INTEGER NOT NULL,
    indexed_at     TEXT NOT NULL
);

CREATE TABLE chunks (
    id             INTEGER PRIMARY KEY,
    chunk_id       TEXT NOT NULL UNIQUE,   -- doc_id:ordinal
    doc_id         TEXT NOT NULL REFERENCES documents(doc_id),
    ordinal        INTEGER NOT NULL,
    heading        TEXT,                   -- breadcrumb de secciones
    text           TEXT NOT NULL,
    token_count    INTEGER NOT NULL,
    has_images     INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    heading, text,
    content='chunks', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE corpus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX idx_documents_app ON documents(app);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_chunks_doc_id ON chunks(doc_id);
