-- First lab case for SQL Server: TOP vs ORDER BY, ISNULL with two arguments, and LIMIT refused
CREATE TABLE orders (id INT PRIMARY KEY, amount DECIMAL(10,2), discount DECIMAL(10,2))
GO
INSERT INTO orders VALUES (1, 25500, NULL), (2, 500, 50), (3, 80000, 1000)
GO
SELECT TOP 2 id, amount FROM orders ORDER BY amount DESC
GO
SELECT id, ISNULL(discount, 0) AS d FROM orders ORDER BY id
GO
SELECT id FROM orders ORDER BY id LIMIT 2
