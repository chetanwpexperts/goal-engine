#!/usr/bin/env bash
# Local MariaDB + PostgreSQL with the test databases the test-suite expects (for dev / nightly runs only).
set -euo pipefail
command -v mysqld >/dev/null || apt-get install -y -qq mariadb-server >/dev/null
command -v psql >/dev/null || apt-get install -y -qq postgresql >/dev/null
service mariadb start >/dev/null 2>&1 || service mysql start >/dev/null 2>&1
service postgresql start >/dev/null 2>&1
sleep 3
mysql -e "CREATE DATABASE IF NOT EXISTS shop_db;
CREATE USER IF NOT EXISTS 'shopapp'@'localhost' IDENTIFIED BY 'app pass';
CREATE USER IF NOT EXISTS 'shopapp'@'127.0.0.1' IDENTIFIED BY 'app pass';
GRANT ALL ON shop_db.* TO 'shopapp'@'localhost'; GRANT ALL ON shop_db.* TO 'shopapp'@'127.0.0.1';
GRANT SELECT ON performance_schema.* TO 'shopapp'@'127.0.0.1'; GRANT SELECT ON performance_schema.* TO 'shopapp'@'localhost';
GRANT SELECT ON mysql.slow_log TO 'shopapp'@'127.0.0.1';
GRANT PROCESS ON *.* TO 'shopapp'@'127.0.0.1'; GRANT PROCESS ON *.* TO 'shopapp'@'localhost';
USE shop_db;
CREATE TABLE IF NOT EXISTS orders(id INT PRIMARY KEY AUTO_INCREMENT, amount DECIMAL(10,2));
INSERT INTO orders(amount) SELECT 100 FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM orders);
INSERT INTO orders(amount) SELECT 250.5 FROM DUAL WHERE (SELECT COUNT(*) FROM orders) < 2;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS created DATETIME DEFAULT CURRENT_TIMESTAMP;
CREATE TABLE IF NOT EXISTS items (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50), price DECIMAL(10,2), note VARCHAR(50), created DATETIME, blob_col VARBINARY(10));
CREATE TABLE IF NOT EXISTS nokey (a INT);
CREATE TABLE IF NOT EXISTS parents (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(20)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS order_lines (id INT AUTO_INCREMENT PRIMARY KEY, parent_id INT, FOREIGN KEY (parent_id) REFERENCES parents(id) ON DELETE CASCADE) ENGINE=InnoDB;
INSERT INTO parents (id, name) SELECT 1, 'a' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM parents);
INSERT INTO order_lines (parent_id) SELECT 1 FROM DUAL WHERE (SELECT COUNT(*) FROM order_lines) = 0;
INSERT INTO order_lines (parent_id) SELECT 1 FROM DUAL WHERE (SELECT COUNT(*) FROM order_lines) = 1;
CREATE TABLE IF NOT EXISTS wp_users (ID INT PRIMARY KEY, user_login VARCHAR(50), user_pass VARCHAR(255), user_email VARCHAR(100));
ALTER TABLE wp_users ADD COLUMN IF NOT EXISTS display_name VARCHAR(250) DEFAULT '', ADD COLUMN IF NOT EXISTS user_registered DATETIME DEFAULT CURRENT_TIMESTAMP,
  ADD COLUMN IF NOT EXISTS user_activation_key VARCHAR(255) DEFAULT '';
CREATE TABLE IF NOT EXISTS wp_usermeta (umeta_id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT, meta_key VARCHAR(255), meta_value LONGTEXT);
CREATE TABLE IF NOT EXISTS wp_options (option_id BIGINT AUTO_INCREMENT PRIMARY KEY, option_name VARCHAR(191) UNIQUE, option_value LONGTEXT, autoload VARCHAR(20) DEFAULT 'yes');
CREATE TABLE IF NOT EXISTS wp_postmeta (meta_id BIGINT AUTO_INCREMENT PRIMARY KEY, post_id BIGINT, meta_key VARCHAR(255), meta_value LONGTEXT, KEY post_id (post_id), KEY meta_key (meta_key(191)));
CREATE TABLE IF NOT EXISTS wp_posts (ID BIGINT AUTO_INCREMENT PRIMARY KEY, post_author BIGINT, post_date DATETIME, post_content LONGTEXT, post_title TEXT,
  post_status VARCHAR(20) DEFAULT 'publish', post_type VARCHAR(20) DEFAULT 'post', post_modified DATETIME);"
su postgres -c "psql -q -tc \"SELECT 1 FROM pg_roles WHERE rolname='shopapp'\" | grep -q 1 || psql -q -c \"CREATE USER shopapp PASSWORD 'app pass';\""
su postgres -c "psql -q -tc \"SELECT 1 FROM pg_database WHERE datname='shop_pg'\" | grep -q 1 || psql -q -c \"CREATE DATABASE shop_pg OWNER shopapp;\""
PGPASSWORD='app pass' psql -h 127.0.0.1 -U shopapp shop_pg -q -c "
CREATE TABLE IF NOT EXISTS orders(id serial primary key, amount numeric(10,2), created timestamptz default now());
INSERT INTO orders(amount) SELECT 100 WHERE (SELECT count(*) FROM orders) = 0;
INSERT INTO orders(amount) SELECT 250.5 WHERE (SELECT count(*) FROM orders) = 1;
CREATE TABLE IF NOT EXISTS items (id serial PRIMARY KEY, name text, price numeric(10,2), note text, created timestamp, blob_col bytea);
CREATE TABLE IF NOT EXISTS nokey (a int);" >/dev/null
echo "TEST-DBS-OK"
