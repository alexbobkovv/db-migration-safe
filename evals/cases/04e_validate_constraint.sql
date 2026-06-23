-- Case 04e (SAFE): rewrite of case 01, step 3b. Validate under SHARE UPDATE EXCLUSIVE
-- (does not block reads/writes). Needs SET lock_timeout too, or eugene flags E9.
SET lock_timeout = '5s';
ALTER TABLE orders VALIDATE CONSTRAINT orders_processed_at_nn;
