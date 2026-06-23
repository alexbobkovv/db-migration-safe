-- Case 06 (UNSAFE, MySQL): exercises the InnoDB heuristic path (no external tool).
-- Expected heuristic findings: mysql-column-type-copy (error),
-- mysql-pin-algorithm-lock (warning), mysql-add-foreign-key (warning).
-- Catalog: mysql-catalog.md.
ALTER TABLE orders MODIFY COLUMN total BIGINT NOT NULL;
ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users (id);
