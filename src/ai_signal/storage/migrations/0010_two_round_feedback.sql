-- 0010_two_round_feedback.sql
-- DEC-018: two-round human feedback on content briefs.
--
-- Round 1 (选题判断, optional): decision/reason/audience/angle/usefulness
--   (already present on feedback since 0001).
-- Round 2 (发布结果, required after publishing): published_at / outcome /
--   lesson. published_url from 0001 rides along with round 2.
--
-- Additive only: three nullable TEXT columns; all validation happens in code
-- (domain models + feedback sync), never in the schema.

-- ISO date YYYY-MM-DD
ALTER TABLE feedback ADD COLUMN published_at TEXT;

-- free-text result recap (reading/likes/favorites/comments)
ALTER TABLE feedback ADD COLUMN outcome TEXT;

-- one-line retro: what changes next time
ALTER TABLE feedback ADD COLUMN lesson TEXT;
