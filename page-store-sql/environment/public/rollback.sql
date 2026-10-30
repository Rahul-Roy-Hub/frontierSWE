CREATE TABLE items (id INT, name TEXT);
INSERT INTO items VALUES (1, 'bolt');
BEGIN;
INSERT INTO items VALUES (2, 'ghost');
ROLLBACK;
SELECT id, name FROM items;
