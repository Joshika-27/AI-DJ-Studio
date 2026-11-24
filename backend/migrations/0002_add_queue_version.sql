-- Migration: 0002_add_queue_version.sql
-- Description: Add persistent queue_version column to dj_lounges (Module 7 Phase 1)
--
-- Note: Fresh databases and test suites receive the queue_version column automatically
-- through SQLAlchemy's Base.metadata.create_all() via init_db(). Existing PostgreSQL
-- databases that already have the dj_lounges table require running this migration.

ALTER TABLE dj_lounges ADD COLUMN IF NOT EXISTS queue_version INTEGER NOT NULL DEFAULT 1;
