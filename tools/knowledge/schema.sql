-- One small shop schema every drafted example runs against (same on all three engines).
CREATE TABLE customers (id INT PRIMARY KEY, name VARCHAR(50), email VARCHAR(80), city VARCHAR(40), created_at DATE);
CREATE TABLE products (id INT PRIMARY KEY, name VARCHAR(50), category VARCHAR(30), price DECIMAL(10,2));
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT, status VARCHAR(20), total DECIMAL(10,2), created_at DATE);
CREATE TABLE order_items (id INT PRIMARY KEY, order_id INT, product_id INT, qty INT, unit_price DECIMAL(10,2));
CREATE TABLE employees (id INT PRIMARY KEY, name VARCHAR(50), manager_id INT, salary DECIMAL(10,2));
INSERT INTO customers VALUES (1,'Asha','asha@x.in','Delhi','2026-01-05'),(2,'Ravi',NULL,'Pune','2026-02-11'),(3,'Neha','neha@x.in','Delhi','2026-03-02');
INSERT INTO products VALUES (1,'Phone','Electronics',15000),(2,'Cable','Electronics',300),(3,'Chair','Furniture',4500);
INSERT INTO orders VALUES (1,1,'paid',15300,'2026-03-01'),(2,1,'pending',4500,'2026-03-05'),(3,2,'paid',300,'2026-04-01');
INSERT INTO order_items VALUES (1,1,1,1,15000),(2,1,2,1,300),(3,2,3,1,4500),(4,3,2,1,300);
INSERT INTO employees VALUES (1,'Boss',NULL,90000),(2,'Neha',1,50000),(3,'Aman',1,45000);
