-- Tables the papers' questions use that the practice setup doesn't create (same on every engine).
DROP TABLE IF EXISTS projects;
CREATE TABLE projects (proj_id INT PRIMARY KEY, proj_name VARCHAR(80) NOT NULL, dept_id INT, budget DECIMAL(12,2), status VARCHAR(20) NOT NULL);
INSERT INTO projects VALUES (1, 'Apollo', 10, 500000, 'active'), (2, 'Atlas', 20, 200000, 'closed'), (3, 'Beacon', 30, 150000, 'active');
