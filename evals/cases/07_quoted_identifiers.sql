-- Case 07 (ROLLBACK): quoted identifiers must invert, not punt to manual.
-- Regression: ISSUE-001 — quoted column / index / constraint names returned
-- "manual rollback required" because the inversion regex anchored the captured
-- identifier with a trailing \b, which never matches a name ending in a quote.
-- Found by /qa on 2026-06-24.
-- Expect: every statement reverses (0 manual, 0 irreversible).
CREATE TABLE "My Orders" (id int);
ALTER TABLE "My Orders" ADD COLUMN "user id" text;
CREATE UNIQUE INDEX CONCURRENTLY "My Idx" ON "My Orders" (id);
ALTER TABLE "My Orders" ADD CONSTRAINT "my chk" CHECK (id > 0) NOT VALID;
