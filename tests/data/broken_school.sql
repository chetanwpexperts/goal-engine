SELECT
    s.id AS student_id,
    s.full_name,
    cl.class_name,
    t.name AS teacher_name,
    hod.name AS hod_name,
    sub.subject_name,
    m.marks,
    m.max_marks,
    (m.marks / m.max_mark) * 100 AS percentage,
    SUM(f.amount) AS fee_paid
FROM students s
LEFT JOIN classes cl
    ON s.class = cl.id
LEFT JOIN marks m
    ON m.student_id = s.id
INNER JOIN subjects sub
    ON m.subject_id = subjects.id
LEFT JOIN teachers t
    ON sub.teacher_id = t.id
LEFT JOIN teachers hod
    ON t.id = hod.hod_id
LEFT JOIN fees f
    ON f.student_id = s.id
LEFT JOIN fees f2
    ON f2.student_id = s.id
WHERE
    cl.class_name = 'Class 10'
    AND f.status = NULL
    AND percentage >= 33
    AND exam_year = 2026
GROUP BY
    s.id
HAVING
    marks > 30
ORDER BY
    student ASC;
