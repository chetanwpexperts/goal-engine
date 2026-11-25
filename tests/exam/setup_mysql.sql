-- ============================================================================
-- MySQL 8.0+ & MariaDB 10.6+ Setup Script: Schema & Sample Data
-- ============================================================================

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS employees_archive;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS departments;
DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS logins;
DROP TABLE IF EXISTS articles;
DROP TABLE IF EXISTS staging_users;
DROP TABLE IF EXISTS users;
SET FOREIGN_KEY_CHECKS = 1;

-- 1. Core HR & E-Commerce Tables (Papers 1, 2, 4, 5)
CREATE TABLE departments (
    dept_id INT PRIMARY KEY,
    dept_name VARCHAR(100) NOT NULL,
    location VARCHAR(100)
) ENGINE=InnoDB;

CREATE TABLE employees (
    emp_id INT PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(120) NOT NULL,
    dept_id INT,
    manager_id INT,
    salary DECIMAL(10, 2) NOT NULL,
    hire_date DATE NOT NULL,
    metadata JSON,
    CONSTRAINT fk_emp_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
    CONSTRAINT fk_emp_mgr FOREIGN KEY (manager_id) REFERENCES employees(emp_id)
) ENGINE=InnoDB;

CREATE TABLE employees_archive (
    emp_id INT PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(120) NOT NULL,
    dept_id INT,
    salary DECIMAL(10, 2)
) ENGINE=InnoDB;

CREATE TABLE customers (
    customer_id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    city VARCHAR(80)
) ENGINE=InnoDB;

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT,
    order_date DATETIME NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    CONSTRAINT fk_ord_cust FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
    item_id INT PRIMARY KEY,
    order_id INT,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    CONSTRAINT fk_item_ord FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB;

-- 2. Analytical & Cross-Dialect Tables (Papers 3 & 5)
CREATE TABLE sales (
    sale_id INT PRIMARY KEY,
    rep_id INT NOT NULL,
    region VARCHAR(50) NOT NULL,
    sale_date DATE NOT NULL,
    revenue DECIMAL(10, 2) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE logins (
    login_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    login_date DATE NOT NULL
) ENGINE=InnoDB;

CREATE TABLE articles (
    article_id INT PRIMARY KEY,
    title VARCHAR(150) NOT NULL,
    stats JSON NOT NULL
) ENGINE=InnoDB;

CREATE TABLE users (
    email VARCHAR(120) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE staging_users (
    email VARCHAR(120) NOT NULL,
    name VARCHAR(100) NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB;

-- ============================================================================
-- Populate Sample Data (Includes Edge Cases: NULLs, Ties, Duplicates, Streaks)
-- ============================================================================

INSERT INTO departments (dept_id, dept_name, location) VALUES
(10, 'Engineering', 'Bengaluru'),
(20, 'Data Science', 'Hyderabad'),
(30, 'Product', 'Pune'),
(40, 'Empty Dept', 'Chandigarh');

INSERT INTO employees (emp_id, first_name, last_name, email, dept_id, manager_id, salary, hire_date, metadata) VALUES
(101, 'Aarav', 'Sharma', 'aarav@company.com', 10, NULL, 150000.00, '2021-03-15', '{"role": "CEO", "skills": ["Strategy", "PostgreSQL"]}'),
(102, 'Ananya', 'Verma', 'ananya@company.com', 10, 101, 115000.00, '2022-06-01', '{"role": "Lead", "skills": ["PostgreSQL", "Python", "MySQL"]}'),
(103, 'Rohan', 'Mehta', 'rohan@company.com', 10, 102, 95000.00, '2023-01-10', '{"role": "Senior", "skills": ["MySQL", "MariaDB"]}'),
(104, 'Priya', 'Nair', 'priya@company.com', 10, 102, 95000.00, '2023-04-20', '{"role": "Senior", "skills": ["PostgreSQL", "Docker"]}'),
(105, 'Vikram', 'Singh', 'vikram@company.com', 20, 101, 120000.00, '2022-02-14', '{"role": "Lead", "skills": ["Python", "Spark"]}'),
(106, 'Neha', 'Gupta', 'neha@company.com', 20, 105, 82000.00, '2024-08-05', '{"role": "Analyst", "skills": ["SQL", "PostgreSQL"]}'),
(107, 'Kabir', 'Das', 'kabir@company.com', 30, 101, 78000.00, '2025-11-12', '{"role": "PM", "skills": ["Agile"]}'),
(108, 'Meera', 'Joshi', 'neha@company.com', 20, 105, 65000.00, DATE_SUB(CURDATE(), INTERVAL 20 DAY), '{"role": "Junior", "skills": ["SQL"]}'),
(109, 'Arjun', 'Rao', 'arjun@company.com', NULL, 102, 52000.00, DATE_SUB(CURDATE(), INTERVAL 10 DAY), '{"role": "Contractor", "skills": ["MariaDB"]}');

INSERT INTO employees_archive (emp_id, first_name, last_name, email, dept_id, salary) VALUES
(103, 'Rohan', 'Mehta', 'rohan@company.com', 10, 95000.00),
(201, 'Siddharth', 'Kapoor', 'sid@company.com', 30, 70000.00);

INSERT INTO customers (customer_id, name, city) VALUES
(1, 'Reliance Retail', 'Mumbai'),
(2, 'Infosys BPM', 'Bengaluru'),
(3, 'Zomato Ops', 'Gurugram'),
(4, 'Freshworks', 'Chennai'),
(5, 'Inactive Corp', 'Delhi');

INSERT INTO orders (order_id, customer_id, order_date, total_amount, status) VALUES
(1001, 1, '2025-01-15 10:30:00', 5000.00, 'Completed'),
(1002, 1, '2025-02-20 14:15:00', 7500.00, 'Completed'),
(1003, 1, '2025-03-05 09:00:00', 1200.00, 'Cancelled'),
(1004, 2, '2025-06-10 11:45:00', 4500.00, 'Completed'),
(1005, 2, '2026-01-05 16:20:00', 6200.00, 'Completed'),
(1006, 2, '2026-01-05 18:00:00', 3100.00, 'Pending'),
(1007, 3, '2026-01-06 08:30:00', 2000.00, 'Completed'),
(1008, 3, '2026-01-07 12:00:00', 2500.00, 'Completed'),
(1009, 3, '2026-01-08 15:10:00', 3000.00, 'Completed'),
(1010, 4, '2025-11-22 17:40:00', 900.00, 'Cancelled');

INSERT INTO order_items (item_id, order_id, product_id, quantity, unit_price) VALUES
(1, 1001, 501, 2, 2500.00),
(2, 1002, 502, 3, 2500.00),
(3, 1004, 501, 1, 4000.00), -- Intentional mismatch vs 4500.00 for Q20!
(4, 1005, 503, 2, 3100.00),
(5, 1007, 504, 4, 500.00);

INSERT INTO sales (sale_id, rep_id, region, sale_date, revenue) VALUES
(1, 10, 'North', '2026-01-01', 1000.00),
(2, 11, 'North', '2026-01-01', 1500.00),
(3, 10, 'North', '2026-01-02', 2000.00),
(4, 10, 'North', '2026-01-03', 2500.00),
(5, 12, 'South', '2026-01-01', 3000.00),
(6, 12, 'South', '2026-01-03', 3500.00);

INSERT INTO logins (user_id, login_date) VALUES
(1, '2026-01-01'), (1, '2026-01-01'), (1, '2026-01-02'), (1, '2026-01-03'), (1, '2026-01-05'),
(2, '2026-01-10'), (2, '2026-01-11'), (2, '2026-01-15');

INSERT INTO articles (article_id, title, stats) VALUES
(1, 'PostgreSQL Internals', '{"views": 10, "likes": 2}'),
(2, 'MySQL vs MariaDB', '{"views": 25, "likes": 5}');

INSERT INTO users (email, name, updated_at) VALUES
('alice@example.com', 'Alice Old', '2026-01-01 10:00:00');

INSERT INTO staging_users (email, name, updated_at) VALUES
('alice@example.com', 'Alice v1', '2026-01-02 10:00:00'),
('alice@example.com', 'Alice Latest', '2026-01-03 12:00:00'),
('bob@example.com', 'Bob New', '2026-01-03 09:00:00');
