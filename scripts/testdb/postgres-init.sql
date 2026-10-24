CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    amount NUMERIC(10,2),
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO orders (amount, created) VALUES
(100.00, NOW() - INTERVAL '40 days'),
(250.50, NOW() - INTERVAL '10 days'),
(500.00, NOW() - INTERVAL '2 days'),
(1000.00, NOW());

CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    name TEXT,
    price NUMERIC(10,2),
    note TEXT,
    created TIMESTAMP,
    blob_col BYTEA
);

CREATE TABLE nokey (
    a INT,
    b TEXT
);

CREATE TABLE scan_test (
    id SERIAL PRIMARY KEY,
    customer_name TEXT,
    status TEXT,
    amount NUMERIC(10,2)
);

INSERT INTO scan_test(customer_name, status, amount)
SELECT
    'customer-' || g,
    CASE WHEN MOD(g, 2) = 0 THEN 'paid' ELSE 'pending' END,
    g * 1.25
FROM generate_series(1, 5000) AS g;
