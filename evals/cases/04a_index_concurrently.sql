-- Case 04a (SAFE): rewrite of case 02. CONCURRENTLY takes ShareUpdateExclusiveLock, so
-- it needs no lock_timeout and triggers no E9/E4. Must run OUTSIDE a transaction.
-- Re-lints clean under squawk + eugene (M2).
CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders (user_id);
