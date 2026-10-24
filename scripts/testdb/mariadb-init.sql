USE shop_db;

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    amount DECIMAL(10,2),
    created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO orders (amount, created) VALUES
(100.00, NOW() - INTERVAL 40 DAY),
(250.50, NOW() - INTERVAL 10 DAY),
(500.00, NOW() - INTERVAL 2 DAY),
(1000.00, NOW());

CREATE TABLE items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50),
    price DECIMAL(10,2),
    note VARCHAR(50),
    created DATETIME,
    blob_col VARBINARY(10)
);

CREATE TABLE nokey (
    a INT,
    b VARCHAR(50)
);

CREATE TABLE scan_test (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_name VARCHAR(100),
    status VARCHAR(20),
    amount DECIMAL(10,2)
);

INSERT INTO scan_test(customer_name, status, amount)
SELECT
    CONCAT('customer-', seq),
    CASE WHEN MOD(seq, 2) = 0 THEN 'paid' ELSE 'pending' END,
    seq * 1.25
FROM (
    SELECT @row := @row + 1 AS seq
    FROM information_schema.columns, (SELECT @row := 0) r
    LIMIT 5000
) x;

CREATE TABLE ge_users (
    ID INT PRIMARY KEY,
    user_login VARCHAR(50),
    user_pass VARCHAR(255),
    user_email VARCHAR(100)
);

CREATE TABLE ge_usermeta (
    umeta_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT,
    meta_key VARCHAR(255),
    meta_value LONGTEXT
);

CREATE TABLE ge_options (
    option_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    option_name VARCHAR(191) UNIQUE,
    option_value LONGTEXT,
    autoload VARCHAR(20) DEFAULT 'yes'
);

CREATE TABLE ge_posts (
    ID BIGINT AUTO_INCREMENT PRIMARY KEY,
    post_author BIGINT,
    post_date DATETIME,
    post_content LONGTEXT,
    post_title TEXT,
    post_status VARCHAR(20) DEFAULT 'publish',
    post_type VARCHAR(20) DEFAULT 'post',
    post_modified DATETIME
);

-- Cascade-delete safety fixture
CREATE TABLE IF NOT EXISTS parents (
    id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS order_lines (
    id INT PRIMARY KEY AUTO_INCREMENT,
    parent_id INT NOT NULL,
    item VARCHAR(100),
    CONSTRAINT fk_order_lines_parent
        FOREIGN KEY (parent_id) REFERENCES parents(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

INSERT IGNORE INTO parents (id, name)
VALUES (1, 'Test parent');

DELETE FROM order_lines WHERE parent_id = 1;

INSERT INTO order_lines (parent_id, item)
VALUES
    (1, 'Line 1'),
    (1, 'Line 2');
