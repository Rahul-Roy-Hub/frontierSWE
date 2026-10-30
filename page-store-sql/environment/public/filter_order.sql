CREATE TABLE items (id INT, name TEXT);
INSERT INTO items VALUES (1, 'bolt'), (2, 'nut'), (3, 'washer');
SELECT name FROM items WHERE id > 1 AND NOT name = 'washer' OR id = 3 ORDER BY name DESC;
