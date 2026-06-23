-- Case 04d (SAFE): rewrite of case 01, step 3a. Add the NOT NULL check NOT VALID (brief
-- lock, no scan). Kept in its own file so its AccessExclusiveLock is not held across the
-- VALIDATE (would trip eugene E4).
SET lock_timeout = '5s';
ALTER TABLE orders ADD CONSTRAINT orders_processed_at_nn CHECK (processed_at IS NOT NULL) NOT VALID;
