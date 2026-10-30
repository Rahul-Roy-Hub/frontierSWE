CREATE TABLE items (id INT, name TEXT);
INSERT INTO items VALUES (1, 'bolt'), (2, 'nut');
SELECT id, name FROM items ORDER BY id ASC;
