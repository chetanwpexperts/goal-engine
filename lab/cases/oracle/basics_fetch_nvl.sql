-- First lab case for Oracle: FETCH FIRST, NVL, an empty string is NULL, and LIMIT refused
CREATE TABLE orders (id NUMBER PRIMARY KEY, amount NUMBER(10,2), note VARCHAR2(20))
/
INSERT INTO orders VALUES (1, 25500, NULL)
/
INSERT INTO orders VALUES (2, 500, '')
/
SELECT id, amount FROM orders ORDER BY amount DESC FETCH FIRST 1 ROWS ONLY
/
SELECT COUNT(*) AS empty_is_null FROM orders WHERE note IS NULL
/
SELECT id FROM orders ORDER BY id LIMIT 1
