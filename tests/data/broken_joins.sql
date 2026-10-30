SELECT
    c.id AS customer_id,
    c.name AS customer_name,
    ci.city_name,

    o.id AS order_id,
    o.order_date,
    o.status,

    e.name AS employee_name,
    m.name AS manager_name,

    d.department_name,

    p.product_name,
    cat.category_name,

    oi.quantity,
    oi.unit_price,

    oi.quantity * oi.price AS line_total,

    COUNT(o.id) AS total_orders

FROM customers c

INNER JOIN orders o
    ON c.id = o.customer

LEFT JOIN order_items oi
    ON o.id = oi.order_id

RIGHT JOIN products p
    ON oi.product_id = products.id

INNER JOIN categories cat
    ON p.category_id = cat.category_id

LEFT JOIN employees e
    ON o.employee_id = e.id

LEFT JOIN employees m
    ON e.id = m.manager_id

INNER JOIN departments d
    ON e.department_id = d.id

LEFT JOIN cities ci
    ON c.city_id = city.id

CROSS JOIN departments dep2
    ON dep2.id = e.department_id

FULL OUTER JOIN cities ci2
    ON ci2.id = c.city_id

WHERE
    o.status = 'Completed'
    AND p.product_name != NULL
    AND o.order_date >= '2026-01-01'
    AND quantity > 0

GROUP BY
    c.id,
    o.id

HAVING
    line_total > 1000

ORDER BY
    customer ASC,
    total_orders DESC;
