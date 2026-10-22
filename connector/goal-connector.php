<?php
/**
 * Goal Engine database connector (PHP). Upload this file as-is with your hosting File Manager.
 *
 * - Finds your database settings by itself (WordPress, Laravel, CodeIgniter). Your password never leaves your hosting.
 * - Only answers requests signed by Goal Engine with this file's secret key, each valid for 2 minutes and only once.
 * - Read-only: only SELECT / SHOW / EXPLAIN, inside a read-only transaction. It can't change your data.
 * - Delete this file any time to disconnect.
 */

// Optional: fill these only if your settings can't be found automatically.
$GOAL_DB = ['type' => '', 'host' => '', 'port' => '', 'database' => '', 'user' => '', 'password' => ''];

define('GOAL_CONNECTOR_ID', '__CONNECTOR_ID__');
define('GOAL_ALLOW_CHANGES', __ALLOW_CHANGES__);   // approved changes with Undo; chosen when you downloaded this file
define('GOAL_MAX_CHANGE_ROWS', 1000);
define('GOAL_UNDO_DAYS', 7);
define('GOAL_KEY', '__KEY__');
define('GOAL_VERSION', '1.0');
define('GOAL_MAX_ROWS', 100000);
define('GOAL_MAX_BYTES', 20 * 1024 * 1024);

@ini_set('display_errors', '0');
@set_time_limit(120);
header('Content-Type: application/json; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');
header('Cache-Control: no-store');

$GOAL_ENCRYPT = false;

// Requests and answers are encrypted with this file's key (AES-256-GCM), so firewalls see no SQL
// and nobody in between can read your data.
function goal_aes_key() { return hash('sha256', 'goal-engine-transport:' . GOAL_KEY, true); }

function goal_seal($text) {
    $iv = random_bytes(12);
    $tag = '';
    $ct = openssl_encrypt($text, 'aes-256-gcm', goal_aes_key(), OPENSSL_RAW_DATA, $iv, $tag);
    return base64_encode($iv . $ct . $tag);
}

function goal_json($data) {
    return json_encode($data, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR);
}

function goal_out($data, $code = 200) {
    global $GOAL_ENCRYPT;
    http_response_code($code);
    $json = goal_json($data);
    if ($GOAL_ENCRYPT) $json = json_encode(['ok' => true, 'sealed' => goal_seal($json)]);
    echo $json;
    exit;
}

// Streaming answers (exports): one encrypted line per piece, so neither side holds the whole file in memory.
function goal_emit($data) {
    echo json_encode(['sealed' => goal_seal(goal_json($data))]) . "\n";
    @flush();
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    goal_out(['ok' => false, 'error' => 'Goal Engine connector. Nothing to see here.'], 404);
}

// ---------------------------------------------------------------- signature check
$body = file_get_contents('php://input', false, null, 0, 1024 * 1024);
$ts = isset($_SERVER['HTTP_X_GOAL_TIME']) ? $_SERVER['HTTP_X_GOAL_TIME'] : '';
$nonce = isset($_SERVER['HTTP_X_GOAL_NONCE']) ? $_SERVER['HTTP_X_GOAL_NONCE'] : '';
$sig = isset($_SERVER['HTTP_X_GOAL_SIGNATURE']) ? $_SERVER['HTTP_X_GOAL_SIGNATURE'] : '';
$expected = hash_hmac('sha256', $ts . "\n" . $nonce . "\n" . $body, GOAL_KEY);
if (!ctype_digit($ts) || abs(time() - (int)$ts) > 120 || !preg_match('/^[A-Za-z0-9_-]{16,64}$/', $nonce) || !hash_equals($expected, $sig)) {
    goal_out(['ok' => false, 'error' => 'Not signed by Goal Engine'], 403);
}
// each signed request works only once
$seen_file = rtrim(sys_get_temp_dir(), '/\\') . '/goal_nonces_' . substr(hash('sha256', GOAL_KEY), 0, 16) . '.json';
$seen = @json_decode(@file_get_contents($seen_file), true);
if (!is_array($seen)) $seen = [];
foreach ($seen as $n => $t) { if ($t < time() - 300) unset($seen[$n]); }
if (isset($seen[$nonce])) goal_out(['ok' => false, 'error' => 'Request already used'], 403);
$seen[$nonce] = time();
@file_put_contents($seen_file, json_encode($seen), LOCK_EX);

if (!function_exists('openssl_decrypt')) goal_out(['ok' => false, 'error' => 'This hosting has PHP without OpenSSL; ask your host to enable it'], 500);
$outer = json_decode($body, true);
$raw = (is_array($outer) && isset($outer['sealed'])) ? base64_decode($outer['sealed'], true) : false;
$job = null;
if ($raw !== false && strlen($raw) > 28) {
    $plain = openssl_decrypt(substr($raw, 12, -16), 'aes-256-gcm', goal_aes_key(), OPENSSL_RAW_DATA, substr($raw, 0, 12), substr($raw, -16));
    if ($plain !== false) $job = json_decode($plain, true);
}
if (!is_array($job) || empty($job['kind'])) goal_out(['ok' => false, 'error' => 'Bad request'], 400);
$GOAL_ENCRYPT = true;

// ---------------------------------------------------------------- finding the database settings
function goal_unquote($v) {
    $v = trim($v);
    if (strlen($v) >= 2 && ($v[0] === '"' || $v[0] === "'") && substr($v, -1) === $v[0]) $v = substr($v, 1, -1);
    return $v;
}

function goal_parse_env($text) {
    $env = [];
    foreach (preg_split('/\r?\n/', $text) as $line) {
        if (preg_match('/^\s*([A-Za-z_][\w.]*)\s*=\s*(.*)$/', $line, $m) && strpos(ltrim($line), '#') !== 0) {
            $env[$m[1]] = goal_unquote(preg_replace('/\s+#.*$/', '', $m[2]));
        }
    }
    if (!empty($env['DB_DATABASE'])) {
        $c = isset($env['DB_CONNECTION']) ? $env['DB_CONNECTION'] : 'mysql';
        $type = in_array($c, ['pgsql', 'postgres']) ? 'pgsql' : (in_array($c, ['mysql', 'mariadb']) ? 'mysql' : '');
        if ($type) return ['type' => $type, 'host' => isset($env['DB_HOST']) ? $env['DB_HOST'] : '127.0.0.1', 'port' => isset($env['DB_PORT']) ? $env['DB_PORT'] : '',
            'database' => $env['DB_DATABASE'], 'user' => isset($env['DB_USERNAME']) ? $env['DB_USERNAME'] : '', 'password' => isset($env['DB_PASSWORD']) ? $env['DB_PASSWORD'] : '', 'app' => 'Laravel'];
    }
    if (!empty($env['database.default.database'])) {
        $d = isset($env['database.default.DBDriver']) ? $env['database.default.DBDriver'] : 'MySQLi';
        return ['type' => $d === 'Postgre' ? 'pgsql' : 'mysql', 'host' => isset($env['database.default.hostname']) ? $env['database.default.hostname'] : 'localhost',
            'port' => isset($env['database.default.port']) ? $env['database.default.port'] : '', 'database' => $env['database.default.database'],
            'user' => isset($env['database.default.username']) ? $env['database.default.username'] : '', 'password' => isset($env['database.default.password']) ? $env['database.default.password'] : '', 'app' => 'CodeIgniter 4'];
    }
    return null;
}

function goal_parse_wp($text) {
    preg_match_all('/define\(\s*[\'"](DB_NAME|DB_USER|DB_PASSWORD|DB_HOST)[\'"]\s*,\s*[\'"](.*?)[\'"]\s*\)/', $text, $m, PREG_SET_ORDER);
    $v = [];
    foreach ($m as $x) $v[$x[1]] = stripcslashes($x[2]);
    if (empty($v['DB_NAME'])) return null;
    $host = isset($v['DB_HOST']) ? $v['DB_HOST'] : 'localhost';
    $port = '';
    if (strpos($host, ':') !== false) { list($host, $port) = explode(':', $host, 2); }
    return ['type' => 'mysql', 'host' => $host ?: 'localhost', 'port' => $port, 'database' => $v['DB_NAME'],
        'user' => isset($v['DB_USER']) ? $v['DB_USER'] : '', 'password' => isset($v['DB_PASSWORD']) ? $v['DB_PASSWORD'] : '', 'app' => 'WordPress'];
}

function goal_parse_ci3($text) {
    preg_match_all('/\$db\[[\'"]default[\'"]\]\[[\'"](hostname|username|password|database|dbdriver|port)[\'"]\]\s*=\s*[\'"]?(.*?)[\'"]?\s*;/', $text, $m, PREG_SET_ORDER);
    $v = [];
    foreach ($m as $x) $v[$x[1]] = $x[2];
    if (empty($v['database'])) return null;
    return ['type' => (isset($v['dbdriver']) && stripos($v['dbdriver'], 'postgre') !== false) ? 'pgsql' : 'mysql', 'host' => isset($v['hostname']) ? $v['hostname'] : 'localhost',
        'port' => isset($v['port']) ? $v['port'] : '', 'database' => $v['database'], 'user' => isset($v['username']) ? $v['username'] : '', 'password' => isset($v['password']) ? $v['password'] : '', 'app' => 'CodeIgniter 3'];
}

function goal_find_settings($manual) {
    if (!empty($manual['database']) && !empty($manual['user'])) {
        $manual['type'] = in_array($manual['type'], ['pgsql', 'postgres', 'postgresql']) ? 'pgsql' : 'mysql';
        $manual['app'] = 'Manual';
        return $manual;
    }
    $dir = __DIR__;
    for ($i = 0; $i < 4 && $dir; $i++) {
        $checks = [$dir . '/wp-config.php' => 'goal_parse_wp', $dir . '/.env' => 'goal_parse_env',
                   $dir . '/application/config/database.php' => 'goal_parse_ci3'];
        foreach ($checks as $file => $parser) {
            if (is_readable($file)) {
                $cfg = $parser((string)@file_get_contents($file, false, null, 0, 200000));
                if ($cfg && $cfg['database']) return $cfg;
            }
        }
        $parent = dirname($dir);
        if ($parent === $dir) break;
        $dir = $parent;
    }
    return null;
}

// ---------------------------------------------------------------- read-only rules (same as Goal Engine's)
function goal_safe_sql($sql) {
    $sql = trim(rtrim(trim((string)$sql), ';'));
    $bare = preg_replace('/\'(?:[^\'\\\\]|\\\\.)*\'|"(?:[^"\\\\]|\\\\.)*"|`[^`]*`/', "''", $sql);
    $bare = preg_replace('/\/\*.*?\*\/|--[^\n]*|#[^\n]*/s', ' ', $bare);
    if ($sql === '' || strpos($bare, ';') !== false) throw new Exception('One statement at a time, please');
    if (!preg_match('/^\s*(select|with|show|describe|desc|explain)\b/i', $bare) ||
        preg_match('/\b(insert|update|delete|replace|merge|drop|alter|create|truncate|rename|grant|revoke|lock|unlock|call|exec|execute|handler|load_file|sleep|benchmark|pg_sleep|copy|vacuum|set|into\s+outfile|into\s+dumpfile)\b/i', $bare)) {
        throw new Exception('This connector is read-only; that query would change data');
    }
    return $sql;
}

function goal_connect($c) {
    $port = $c['port'] ? $c['port'] : ($c['type'] === 'pgsql' ? '5432' : '3306');
    if ($c['type'] === 'pgsql') {
        $dsn = 'pgsql:host=' . $c['host'] . ';port=' . $port . ';dbname=' . $c['database'];
    } elseif ($port !== '' && !ctype_digit($port)) {
        $dsn = 'mysql:unix_socket=' . $port . ';dbname=' . $c['database'] . ';charset=utf8mb4';
    } else {
        $dsn = 'mysql:host=' . $c['host'] . ';port=' . $port . ';dbname=' . $c['database'] . ';charset=utf8mb4';
    }
    $opts = [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 10,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_NUM, PDO::ATTR_EMULATE_PREPARES => false];
    // an UPDATE reports the rows it matched, so we can check it touched exactly the previewed rows
    if ($c['type'] !== 'pgsql' && defined('PDO::MYSQL_ATTR_FOUND_ROWS')) $opts[PDO::MYSQL_ATTR_FOUND_ROWS] = true;
    $pdo = new PDO($dsn, $c['user'], $c['password'], $opts);
    return $pdo;
}

function goal_begin($pdo, $type) {
    if ($type === 'pgsql') {
        $pdo->exec('BEGIN READ ONLY');
        $pdo->exec("SET LOCAL statement_timeout = '90s'");
    } else {
        foreach (['SET SESSION MAX_EXECUTION_TIME=90000', 'SET SESSION max_statement_time=90'] as $q) { try { $pdo->exec($q); break; } catch (Exception $e) {} }
        $pdo->exec('SET SESSION TRANSACTION READ ONLY');
        $pdo->exec('START TRANSACTION READ ONLY');
    }
}

function goal_rows($pdo, $type, $sql, $limit = 0) {
    goal_begin($pdo, $type);
    try {
        $st = $pdo->query($sql);
        $cols = [];
        for ($i = 0; $i < $st->columnCount(); $i++) { $m = $st->getColumnMeta($i); $cols[] = $m['name']; }
        $rows = [];
        $hidden = goal_hidden($cols);
        while (($r = $st->fetch()) !== false) {
            $rows[] = goal_mask(array_map('goal_clean', $r), $hidden);
            if ($limit && count($rows) >= $limit) break;
        }
        $st->closeCursor();
    } finally {
        try { $pdo->exec('ROLLBACK'); } catch (Exception $e) {}
    }
    return [$cols, $rows];
}

// Columns that hold secrets are never sent, even in a preview (WordPress user_pass, API tokens, ...)
function goal_hidden($cols) {
    $h = [];
    foreach ($cols as $i => $c) {
        $n = strtolower(preg_replace('/([a-z0-9])([A-Z])/', '$1_$2', (string)$c));   // apiToken -> api_token
        if (preg_match('/(^|_)(pass|password|passwd|pwd|secret|secrets|token|tokens|salt|hash|otp|cvv|pin)($|_)|api_?key|private_?key|card_?number|session/', $n)) $h[$i] = true;
    }
    return $h;
}

function goal_mask($row, $hidden) {
    foreach ($hidden as $i => $_) if (isset($row[$i]) && $row[$i] !== null) $row[$i] = "\u{2022}\u{2022}\u{2022}\u{2022}";
    return $row;
}

function goal_clean($v) {
    if ($v === null || is_int($v) || is_float($v) || is_bool($v)) return $v;
    $v = (string)$v;
    if (!mb_check_encoding($v, 'UTF-8')) return '<' . strlen($v) . ' bytes>';
    return $v;
}

function goal_can_write($pdo, $type) {
    try {
        if ($type === 'pgsql') {
            list(, $r) = goal_rows($pdo, $type, "SELECT count(*) FROM information_schema.table_privileges WHERE grantee = current_user AND privilege_type IN ('INSERT','UPDATE','DELETE')");
            list(, $su) = goal_rows($pdo, $type, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user");
            return (bool)$r[0][0] || (!empty($su) && $su[0][0]);
        }
        list(, $g) = goal_rows($pdo, $type, 'SHOW GRANTS');
        $text = strtoupper(implode(' ', array_map(function ($x) { return $x[0]; }, $g)));
        return (bool)preg_match('/ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP/', $text);
    } catch (Exception $e) { return false; }
}

function goal_schema($pdo, $type) {
    if ($type === 'pgsql') {
        list(, $rows) = goal_rows($pdo, $type, "SELECT c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname = 'public' ORDER BY c.relname");
        list(, $cols) = goal_rows($pdo, $type, "SELECT table_name, column_name, data_type, '' FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position");
    } else {
        list(, $rows) = goal_rows($pdo, $type, "SELECT table_name, table_rows, data_length + index_length FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name");
        list(, $cols) = goal_rows($pdo, $type, "SELECT table_name, column_name, column_type, column_key FROM information_schema.columns WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position");
    }
    $tables = [];
    foreach ($rows as $r) $tables[$r[0]] = ['name' => $r[0], 'rows' => (int)$r[1], 'bytes' => (int)$r[2], 'columns' => []];
    foreach ($cols as $c) if (isset($tables[$c[0]])) $tables[$c[0]]['columns'][] = ['name' => $c[1], 'type' => (string)$c[2], 'key' => (string)$c[3]];
    return ['tables' => array_values($tables)];
}

function goal_version($pdo, $type) {
    list(, $r) = goal_rows($pdo, $type, $type === 'pgsql' ? 'SHOW server_version' : 'SELECT VERSION()');
    return (string)$r[0][0];
}

function goal_health($pdo, $type) {
    $out = ['version' => goal_version($pdo, $type), 'checks' => []];
    $schema = goal_schema($pdo, $type)['tables'];
    usort($schema, function ($a, $b) { return ($b['bytes'] ?: $b['rows']) - ($a['bytes'] ?: $a['rows']); });
    $out['biggest'] = array_map(function ($t) { return ['name' => $t['name'], 'rows' => $t['rows'], 'bytes' => $t['bytes']]; }, array_slice($schema, 0, 5));
    if ($type !== 'pgsql') {
        $nokey = [];
        foreach ($schema as $t) { $pk = false; foreach ($t['columns'] as $c) if ($c['key'] === 'PRI') $pk = true; if (!$pk) $nokey[] = $t['name']; }
        if ($nokey) $out['checks'][] = ['level' => 'warn', 'text' => count($nokey) . ' table(s) have no primary key: ' . implode(', ', array_slice($nokey, 0, 8))];
        try {
            list(, $big) = goal_rows($pdo, $type, "SELECT t.table_name, t.table_rows FROM information_schema.tables t WHERE t.table_schema = DATABASE() AND t.table_rows > 10000 AND NOT EXISTS (SELECT 1 FROM information_schema.statistics s WHERE s.table_schema = t.table_schema AND s.table_name = t.table_name AND s.index_name <> 'PRIMARY')");
            foreach (array_slice($big, 0, 8) as $b) $out['checks'][] = ['level' => 'warn', 'text' => $b[0] . ' has ' . $b[1] . ' rows but no index besides the primary key'];
        } catch (Exception $e) {}
    }
    if (!$out['checks']) $out['checks'][] = ['level' => 'ok', 'text' => 'No problems found'];
    return $out;
}

function goal_export_stream($pdo, $type, $sql) {
    header('Content-Type: application/x-ndjson');
    while (ob_get_level()) @ob_end_clean();
    $count = 0;
    $limited = false;
    $total = 0;
    $buf = "\xEF\xBB\xBF";
    $line = fopen('php://temp', 'w+');
    $put = function ($row) use ($line, &$buf) {
        ftruncate($line, 0); rewind($line);
        fputcsv($line, $row);
        rewind($line);
        $buf .= stream_get_contents($line);
    };
    goal_begin($pdo, $type);
    try {
        $st = $pdo->query($sql);
        $cols = [];
        for ($i = 0; $i < $st->columnCount(); $i++) { $m = $st->getColumnMeta($i); $cols[] = $m['name']; }
        $hidden = goal_hidden($cols);
        $put($cols);
        while (($r = $st->fetch()) !== false) {
            $put(goal_mask(array_map('goal_clean', $r), $hidden));
            $count++;
            if (strlen($buf) >= 262144) {               // send every ~256 KB, compressed
                $total += strlen($buf);
                goal_emit(['part' => base64_encode(gzencode($buf, 6))]);
                $buf = '';
            }
            if ($count >= GOAL_MAX_ROWS || $total > GOAL_MAX_BYTES) { $limited = true; break; }
        }
        $st->closeCursor();
        if ($buf !== '') goal_emit(['part' => base64_encode(gzencode($buf, 6))]);
        goal_emit(['ok' => true, 'result' => ['row_count' => $count, 'limited' => $limited]]);
    } catch (Exception $e) {
        goal_emit(['ok' => false, 'error' => substr($e->getMessage(), 0, 300)]);
    }
    try { $pdo->exec('ROLLBACK'); } catch (Exception $e) {}
    exit;
}

// ---------------------------------------------------------------- live view (values in query text are replaced by ?)
function goal_shape($sql) {
    $sql = preg_replace('/\'(?:[^\'\\\\]|\\\\.)*\'|"(?:[^"\\\\]|\\\\.)*"/', '?', (string)$sql);
    $sql = preg_replace('/\b\d+(\.\d+)?\b/', '?', $sql);
    return substr(trim(preg_replace('/\s+/', ' ', $sql)), 0, 300);
}

function goal_section($fn) {
    try { return array_merge(['available' => true], $fn()); }
    catch (Exception $e) {
        $m = $e->getMessage();
        $why = preg_match('/denied|permission|privilege|1142|1227/i', $m) ? "This database user isn't allowed to see it"
             : (preg_match("/doesn't exist|does not exist|1146/i", $m) ? 'Not switched on in this database server' : substr($m, 0, 120));
        return ['available' => false, 'reason' => $why];
    }
}

function goal_live($pdo, $type) {
    $out = ['at' => gmdate('c')];
    $na = ['available' => false, 'reason' => 'Not available for this database'];
    if ($type === 'pgsql') {
        $out['status'] = goal_section(function () use ($pdo, $type) {
            list(, $r) = goal_rows($pdo, $type, "SELECT (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()), (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active'), xact_commit, xact_rollback, deadlocks, round(100.0 * blks_hit / greatest(blks_hit + blks_read, 1), 1) FROM pg_stat_database WHERE datname = current_database()");
            return ['values' => array_combine(['connections', 'active', 'commits', 'rollbacks', 'deadlocks', 'cache_hit_%'], $r[0])];
        });
        $out['running'] = goal_section(function () use ($pdo, $type) {
            list(, $r) = goal_rows($pdo, $type, "SELECT pid, usename, extract(epoch FROM now() - query_start)::int, state, query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle' ORDER BY query_start LIMIT 30");
            return ['columns' => ['pid', 'user', 'seconds', 'state', 'query'], 'rows' => array_map(function ($x) { $x[4] = goal_shape($x[4]); return $x; }, $r)];
        });
        $out['top'] = goal_section(function () use ($pdo, $type) {
            list(, $r) = goal_rows($pdo, $type, "SELECT query, calls, round((total_exec_time / 1000)::numeric, 3), round(mean_exec_time::numeric, 1), rows FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) ORDER BY total_exec_time DESC LIMIT 15");
            return ['columns' => ['query', 'runs', 'total s', 'avg ms', 'rows'], 'rows' => array_map(function ($x) { $x[0] = goal_shape($x[0]); return $x; }, $r)];
        });
        $out['slow'] = $na;
        $out['errors'] = $na;
        return $out;
    }
    $out['status'] = goal_section(function () use ($pdo, $type) {
        list(, $r) = goal_rows($pdo, $type, "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Questions','Slow_queries','Uptime','Aborted_connects')");
        $v = [];
        foreach ($r as $x) $v[$x[0]] = $x[1];
        return ['values' => $v];
    });
    $out['running'] = goal_section(function () use ($pdo, $type) {
        list(, $r) = goal_rows($pdo, $type, "SELECT ID, USER, TIME, STATE, INFO FROM information_schema.PROCESSLIST WHERE COMMAND <> 'Sleep' AND ID <> CONNECTION_ID() AND INFO IS NOT NULL ORDER BY TIME DESC LIMIT 30");
        return ['columns' => ['id', 'user', 'seconds', 'state', 'query'], 'rows' => array_map(function ($x) { $x[4] = goal_shape($x[4]); return $x; }, $r)];
    });
    $out['top'] = goal_section(function () use ($pdo, $type) {
        list(, $r) = goal_rows($pdo, $type, "SELECT DIGEST_TEXT, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e12, 3), ROUND(AVG_TIMER_WAIT/1e9, 1), SUM_ROWS_EXAMINED FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME = DATABASE() AND DIGEST_TEXT NOT LIKE '%performance_schema%' AND DIGEST_TEXT NOT LIKE '%information_schema%' AND DIGEST_TEXT NOT LIKE 'SET %' AND DIGEST_TEXT NOT LIKE 'START TRANSACTION%' AND DIGEST_TEXT NOT LIKE 'ROLLBACK%' ORDER BY SUM_TIMER_WAIT DESC LIMIT 15");
        return ['columns' => ['query', 'runs', 'total s', 'avg ms', 'rows read'], 'rows' => array_map(function ($x) { $x[0] = goal_shape($x[0]); return $x; }, $r)];
    });
    $out['slow'] = goal_section(function () use ($pdo, $type) {
        list(, $r) = goal_rows($pdo, $type, "SELECT start_time, query_time, rows_examined, sql_text FROM mysql.slow_log WHERE db = DATABASE() AND sql_text NOT LIKE 'SET %' AND sql_text NOT LIKE 'START TRANSACTION%' AND sql_text NOT LIKE 'ROLLBACK%' AND sql_text NOT LIKE 'SHOW %' AND sql_text NOT LIKE '%performance_schema.%' AND sql_text NOT LIKE '%information_schema.%' AND sql_text NOT LIKE '%mysql.slow_log%' ORDER BY start_time DESC LIMIT 20");
        return ['columns' => ['when', 'seconds', 'rows read', 'query'], 'rows' => array_map(function ($x) { $x[3] = goal_shape($x[3]); return $x; }, $r)];
    });
    $out['errors'] = goal_section(function () use ($pdo, $type) {
        list(, $r) = goal_rows($pdo, $type, "SELECT LOGGED, PRIO, ERROR_CODE, DATA FROM performance_schema.error_log ORDER BY LOGGED DESC LIMIT 20");
        return ['columns' => ['when', 'level', 'code', 'message'], 'rows' => $r];
    });
    return $out;
}

// ---------------------------------------------------------------- approved changes with Undo
function goal_mask_sql($sql) {
    $keep = function ($m) { $n = strlen($m[0]); return $n >= 2 ? $m[0][0] . str_repeat(' ', $n - 2) . $m[0][$n - 1] : ' '; };
    $sql = preg_replace_callback("/'(?:[^'\\\\]|\\\\.)*'/", $keep, $sql);
    $sql = preg_replace_callback('/"(?:[^"\\\\]|\\\\.)*"/', $keep, $sql);
    return preg_replace_callback('/\/\*.*?\*\/|--[^\n]*|#[^\n]*/s', function ($m) { return str_repeat(' ', strlen($m[0])); }, $sql);
}

function goal_parse_change($sql) {
    $sql = trim(rtrim(trim((string)$sql), ';'));
    $m = goal_mask_sql($sql);
    if ($sql === '' || strpos($m, ';') !== false) throw new Exception('One statement at a time, please');
    if (preg_match('/\b(drop|alter|create|truncate|rename|grant|revoke|lock|unlock|call|exec|execute|handler|load_file|sleep|benchmark|pg_sleep|copy|vacuum|returning|into\s+outfile|into\s+dumpfile|on\s+duplicate|on\s+conflict)\b/i', $m))
        throw new Exception('Only simple UPDATE, DELETE or INSERT ... VALUES changes are allowed');
    $id = '(?P<table>`[^`]*`|"[^"]*"|[A-Za-z_][\w$]*)';
    $forms = ['update' => '/^\s*update\s+' . $id . '\s+set\s+(?P<set>.+?)\s+where\s+(?P<where>.+)$/is',
              'delete' => '/^\s*delete\s+from\s+' . $id . '\s+where\s+(?P<where>.+)$/is',
              'insert' => '/^\s*insert\s+into\s+' . $id . '\s*(?P<cols>\([^)]*\))?\s*values\s*(?P<values>\(.+)$/is'];
    foreach ($forms as $op => $re) {
        if (!preg_match($re, $m, $g, PREG_OFFSET_CAPTURE)) continue;
        if ($op === 'insert' && preg_match('/\bselect\b/i', substr($m, $g['values'][1]))) throw new Exception("INSERT ... SELECT isn't supported; use INSERT ... VALUES");
        if ($op === 'update' && preg_match('/\bjoin\b/i', substr($m, 0, $g['set'][1]))) throw new Exception('Changes work on one table at a time');
        $table = trim(substr($sql, $g['table'][1], strlen($g['table'][0])), '`"');
        $where = $op === 'insert' ? null : substr($sql, $g['where'][1], strlen($g['where'][0]));
        return ['op' => $op, 'table' => $table, 'where' => $where, 'sql' => $sql];
    }
    throw new Exception('Write it as UPDATE table SET ... WHERE ..., DELETE FROM table WHERE ..., or INSERT INTO table (...) VALUES (...)');
}

function goal_q($type, $ident) { return $type === 'pgsql' ? '"' . str_replace('"', '""', $ident) . '"' : '`' . str_replace('`', '``', $ident) . '`'; }

function goal_pk($pdo, $type, $table) {
    if ($type === 'pgsql') {
        $st = $pdo->prepare("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) WHERE i.indrelid = (quote_ident(current_schema()) || '.' || quote_ident(?))::regclass AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)");
        $st->execute([$table]);
        return [array_map(function ($r) { return $r[0]; }, $st->fetchAll()), true];
    }
    $st = $pdo->prepare("SELECT k.COLUMN_NAME, c.EXTRA FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.COLUMNS c ON c.TABLE_SCHEMA = k.TABLE_SCHEMA AND c.TABLE_NAME = k.TABLE_NAME AND c.COLUMN_NAME = k.COLUMN_NAME WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = ? AND k.CONSTRAINT_NAME = 'PRIMARY' ORDER BY k.ORDINAL_POSITION");
    $st->execute([$table]);
    $rows = $st->fetchAll();
    $auto = false;
    foreach ($rows as $r) if (stripos((string)$r[1], 'auto_increment') !== false) $auto = true;
    return [array_map(function ($r) { return $r[0]; }, $rows), $auto];
}

// keep a value exactly so Undo can put it back
function goal_enc($v) {
    if (is_resource($v)) $v = stream_get_contents($v);
    if ($v === null || is_int($v) || is_float($v) || is_bool($v)) return $v;
    $v = (string)$v;
    return mb_check_encoding($v, 'UTF-8') ? $v : ['$b' => base64_encode($v)];
}

function goal_bind($st, $values) {
    $i = 1;
    foreach ($values as $v) {
        if (is_array($v) && isset($v['$b'])) $st->bindValue($i++, base64_decode($v['$b']), PDO::PARAM_LOB);
        elseif ($v === null) $st->bindValue($i++, null, PDO::PARAM_NULL);
        else $st->bindValue($i++, is_bool($v) ? ($v ? 1 : 0) : (string)$v, PDO::PARAM_STR);
    }
    return $st;
}

function goal_by_pk($pdo, $type, $table, $pk, $keys, $lock = false) {
    if (!$keys) return [[], []];
    $one = '(' . implode(' AND ', array_map(function ($c) use ($type) { return goal_q($type, $c) . ' = ?'; }, $pk)) . ')';
    $st = $pdo->prepare('SELECT * FROM ' . goal_q($type, $table) . ' WHERE ' . implode(' OR ', array_fill(0, count($keys), $one)) . ($lock ? ' FOR UPDATE' : ''));
    goal_bind($st, array_merge(...$keys))->execute();
    $cols = [];
    for ($i = 0; $i < $st->columnCount(); $i++) { $m = $st->getColumnMeta($i); $cols[] = $m['name']; }
    return [$cols, array_map(function ($r) { return array_map('goal_enc', $r); }, $st->fetchAll())];
}

// rows in OTHER tables that a delete/update here would change (cascade), and triggers: Undo can't cover those
function goal_side_effects($pdo, $type, $table) {
    try {
        if ($type === 'pgsql') {
            $a = $pdo->prepare("SELECT DISTINCT c.conrelid::regclass::text FROM pg_constraint c WHERE c.contype = 'f' AND c.confrelid = (quote_ident(current_schema()) || '.' || quote_ident(?))::regclass AND (c.confdeltype IN ('c','n','d') OR c.confupdtype IN ('c','n','d'))");
            $b = $pdo->prepare("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = (quote_ident(current_schema()) || '.' || quote_ident(?))::regclass");
        } else {
            $a = $pdo->prepare("SELECT DISTINCT TABLE_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME = ? AND (DELETE_RULE IN ('CASCADE','SET NULL','SET DEFAULT') OR UPDATE_RULE IN ('CASCADE','SET NULL','SET DEFAULT'))");
            $b = $pdo->prepare("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE EVENT_OBJECT_SCHEMA = DATABASE() AND EVENT_OBJECT_TABLE = ?");
        }
        $a->execute([$table]); $b->execute([$table]);
        $f = function ($r) { return $r[0]; };
        return [array_map($f, $a->fetchAll()), array_map($f, $b->fetchAll())];
    } catch (Exception $e) { return [[], []]; }
}

function goal_write_begin($pdo, $type) {
    try { $pdo->exec('ROLLBACK'); } catch (Exception $e) {}
    if ($type !== 'pgsql') $pdo->exec('SET SESSION TRANSACTION READ WRITE');
    $pdo->beginTransaction();
}

function goal_plan($pdo, $type, $sql) {
    $ch = goal_parse_change($sql);
    list($pk, $auto) = goal_pk($pdo, $type, $ch['table']);
    $out = ['op' => $ch['op'], 'table' => $ch['table'], 'pk' => $pk];
    if (!$pk) $out['blocked'] = 'This table has no primary key, so a change couldn\'t be undone safely';
    list($cascades, $triggers) = goal_side_effects($pdo, $type, $ch['table']);
    if ($cascades && $ch['op'] !== 'insert') $out['blocked'] = 'Rows in ' . implode(', ', array_slice($cascades, 0, 5)) . ' are linked to this table and would be ' . ($ch['op'] === 'delete' ? 'deleted' : 'changed') . " too, which Undo can't bring back. Not allowed";
    if ($triggers) $out['warning'] = 'This table has triggers (' . implode(', ', array_slice($triggers, 0, 5)) . "); anything they change elsewhere isn't covered by Undo";
    if ($ch['op'] === 'insert') {
        $out['count'] = null;
        if ($type !== 'pgsql' && !($auto && count($pk) === 1)) $out['blocked'] = 'INSERT can be undone only on tables with an auto-increment id';
        return $out;
    }
    $t = goal_q($type, $ch['table']);
    list($cols, $rows) = goal_rows($pdo, $type, "SELECT * FROM $t WHERE {$ch['where']}", 20);
    list(, $n) = goal_rows($pdo, $type, "SELECT COUNT(*) FROM $t WHERE {$ch['where']}");
    $out['count'] = (int)$n[0][0];
    $out['columns'] = $cols;
    $out['rows'] = $rows;
    if ($out['count'] > GOAL_MAX_CHANGE_ROWS) $out['blocked'] = "This would change {$out['count']} rows; the limit is " . GOAL_MAX_CHANGE_ROWS . ' per change. Narrow the WHERE';
    return $out;
}

function goal_apply($pdo, $type, $sql, $expected) {
    $ch = goal_parse_change($sql);
    $table = $ch['table'];
    list($pk, $auto) = goal_pk($pdo, $type, $table);
    if (!$pk) throw new Exception("This table has no primary key, so the change couldn't be undone safely. Nothing was changed");
    if ($ch['op'] !== 'insert') { list($cascades) = goal_side_effects($pdo, $type, $table); if ($cascades) throw new Exception("Other tables are linked with cascade to this one, so Undo couldn't bring everything back. Nothing was changed"); }
    $t = goal_q($type, $table);
    goal_write_begin($pdo, $type);
    try {
        $before = []; $after = []; $cols = [];
        if ($ch['op'] !== 'insert') {
            $st = $pdo->query("SELECT * FROM $t WHERE {$ch['where']} FOR UPDATE");
            for ($i = 0; $i < $st->columnCount(); $i++) { $m = $st->getColumnMeta($i); $cols[] = $m['name']; }
            $before = array_map(function ($r) { return array_map('goal_enc', $r); }, $st->fetchAll());
            if ($expected !== null && count($before) !== (int)$expected) throw new Exception("The data changed since the preview ($expected rows then, " . count($before) . ' now). Preview again');
            if (count($before) > GOAL_MAX_CHANGE_ROWS) throw new Exception('More than ' . GOAL_MAX_CHANGE_ROWS . ' rows. Narrow the WHERE');
            $n = $pdo->exec($ch['sql']);
            if ($n !== count($before)) throw new Exception('The change touched a different set of rows than the preview. Nothing was changed');
            $idx = array_map(function ($c) use ($cols) { return array_search($c, $cols); }, $pk);
            $keys = array_map(function ($r) use ($idx) { return array_map(function ($i) use ($r) { return $r[$i]; }, $idx); }, $before);
            if ($ch['op'] === 'update') list(, $after) = goal_by_pk($pdo, $type, $table, $pk, $keys);
        } else {
            if ($type === 'pgsql') {
                $st = $pdo->query($ch['sql'] . ' RETURNING ' . implode(', ', array_map(function ($c) use ($type) { return goal_q($type, $c); }, $pk)));
                $keys = $st->fetchAll();
            } else {
                if (!($auto && count($pk) === 1)) throw new Exception('INSERT can be undone only on tables with an auto-increment id. Nothing was changed');
                $n = $pdo->exec($ch['sql']);
                $first = (int)$pdo->lastInsertId();
                $keys = [];
                for ($i = 0; $i < $n; $i++) $keys[] = [$first + $i];
            }
            if (count($keys) > GOAL_MAX_CHANGE_ROWS) throw new Exception('More than ' . GOAL_MAX_CHANGE_ROWS . ' rows. Insert fewer at a time');
            list($cols, $after) = goal_by_pk($pdo, $type, $table, $pk, $keys);
        }
        $pdo->commit();
    } catch (Exception $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        throw $e;
    }
    $record = ['op' => $ch['op'], 'table' => $table, 'pk' => $pk, 'columns' => $cols, 'before' => $before, 'after' => $after];
    return [$record, ['op' => $ch['op'], 'table' => $table, 'rows' => $ch['op'] === 'insert' ? count($after) : count($before)]];
}

function goal_undo($pdo, $type, $rec, $force) {
    $table = $rec['table']; $pk = $rec['pk']; $cols = $rec['columns'];
    $idx = array_map(function ($c) use ($cols) { return array_search($c, $cols); }, $pk);
    $keyof = function ($r) use ($idx) { return array_map(function ($i) use ($r) { return $r[$i]; }, $idx); };
    $t = goal_q($type, $table);
    goal_write_begin($pdo, $type);
    try {
        $rows = in_array($rec['op'], ['update', 'insert']) ? $rec['after'] : $rec['before'];
        list(, $current) = goal_by_pk($pdo, $type, $table, $pk, array_map($keyof, $rows), true);
        $now = [];
        foreach ($current as $r) $now[json_encode($keyof($r))] = $r;
        $conflicts = 0;
        if ($rec['op'] === 'delete') $conflicts = count($now);
        else foreach ($rec['after'] as $r) { $k = json_encode($keyof($r)); if (!isset($now[$k]) || $now[$k] != $r) $conflicts++; }
        if ($conflicts && !$force) throw new Exception("CONFLICT: $conflicts of these rows were changed again after this change. Undo would overwrite the newer edits");
        $wherepk = implode(' AND ', array_map(function ($c) use ($type) { return goal_q($type, $c) . ' = ?'; }, $pk));
        if ($rec['op'] === 'update') {
            $others = array_values(array_filter(array_keys($cols), function ($i) use ($cols, $pk) { return !in_array($cols[$i], $pk); }));
            $st = $pdo->prepare("UPDATE $t SET " . implode(', ', array_map(function ($i) use ($cols, $type) { return goal_q($type, $cols[$i]) . ' = ?'; }, $others)) . " WHERE $wherepk");
            foreach ($rec['before'] as $r) goal_bind($st, array_merge(array_map(function ($i) use ($r) { return $r[$i]; }, $others), $keyof($r)))->execute();
        } elseif ($rec['op'] === 'delete') {
            $st = $pdo->prepare("INSERT INTO $t (" . implode(', ', array_map(function ($c) use ($type) { return goal_q($type, $c); }, $cols)) . ') VALUES (' . implode(', ', array_fill(0, count($cols), '?')) . ')');
            foreach ($rec['before'] as $r) { if (isset($now[json_encode($keyof($r))])) continue; goal_bind($st, $r)->execute(); }
        } else {
            $st = $pdo->prepare("DELETE FROM $t WHERE $wherepk");
            foreach ($rec['after'] as $r) goal_bind($st, $keyof($r))->execute();
        }
        $pdo->commit();
    } catch (Exception $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        throw $e;
    }
    return ['op' => 'undo', 'table' => $table, 'rows' => count($rec['op'] === 'insert' ? $rec['after'] : $rec['before']), 'forced' => (bool)($force && $conflicts)];
}

// The copy for Undo stays on this hosting, encrypted, outside the website folder when possible.
function goal_undo_dir() {
    foreach ([dirname(__DIR__), sys_get_temp_dir()] as $base) {
        $dir = rtrim($base, '/\\') . '/.goal-undo-' . substr(hash('sha256', GOAL_KEY), 0, 12);
        if (is_dir($dir) || @mkdir($dir, 0700)) {
            if (!file_exists($dir . '/.htaccess')) @file_put_contents($dir . '/.htaccess', "Require all denied\nDeny from all\n");
            foreach (glob($dir . '/*.undo') ?: [] as $f) if (filemtime($f) < time() - GOAL_UNDO_DAYS * 86400) @unlink($f);
            return $dir;
        }
    }
    throw new Exception('No folder to keep the Undo copy in, so the change was not made');
}

function goal_undo_path($id) { return goal_undo_dir() . '/' . (int)$id . '.undo'; }

// ---------------------------------------------------------------- run the job
$cfg = goal_find_settings($GOAL_DB);
if (!$cfg) goal_out(['ok' => false, 'error' => "Couldn't find your database settings next to this file. Upload it into your website's main folder (where wp-config.php or .env is)."]);
try {
    $pdo = goal_connect($cfg);
} catch (Exception $e) {
    $m = $e->getMessage();
    if (strpos($m, '1045') !== false || stripos($m, 'access denied') !== false || stripos($m, 'password authentication') !== false) $m = 'The database said no to the user/password found in your settings';
    goal_out(['ok' => false, 'error' => 'Couldn\'t open your database: ' . substr($m, 0, 200)]);
}
$type = $cfg['type'];
$payload = isset($job['payload']) && is_array($job['payload']) ? $job['payload'] : [];
try {
    switch ($job['kind']) {
        case 'hello':
            goal_out(['ok' => true, 'result' => ['version' => GOAL_VERSION, 'php' => PHP_VERSION, 'db_type' => $type === 'pgsql' ? 'postgres' : 'mysql',
                'db_name' => $cfg['database'], 'db_version' => goal_version($pdo, $type), 'can_write' => goal_can_write($pdo, $type), 'app' => $cfg['app'],
                'allow_changes' => GOAL_ALLOW_CHANGES]]);
        case 'plan':
            if (!GOAL_ALLOW_CHANGES) goal_out(['ok' => false, 'error' => 'Changes are switched off in this file']);
            goal_out(['ok' => true, 'result' => goal_plan($pdo, $type, isset($payload['sql']) ? $payload['sql'] : '')]);
        case 'change':
            if (!GOAL_ALLOW_CHANGES) goal_out(['ok' => false, 'error' => 'Changes are switched off in this file']);
            $path = goal_undo_path(isset($job['id']) ? $job['id'] : 0);   // checked before anything changes
            list($rec, $info) = goal_apply($pdo, $type, isset($payload['sql']) ? $payload['sql'] : '', isset($payload['expected']) ? $payload['expected'] : null);
            file_put_contents($path, goal_seal(goal_json($rec)), LOCK_EX);
            @chmod($path, 0600);
            $info['undo'] = ['available' => true, 'where' => 'your hosting'];
            goal_out(['ok' => true, 'result' => $info]);
        case 'undo':
            if (!GOAL_ALLOW_CHANGES) goal_out(['ok' => false, 'error' => 'Changes are switched off in this file']);
            $path = goal_undo_path(isset($payload['change_job_id']) ? $payload['change_job_id'] : 0);
            $raw = is_readable($path) ? base64_decode((string)file_get_contents($path)) : false;
            $rec = null;
            if ($raw !== false && strlen($raw) > 28) {
                $plain = openssl_decrypt(substr($raw, 12, -16), 'aes-256-gcm', goal_aes_key(), OPENSSL_RAW_DATA, substr($raw, 0, 12), substr($raw, -16));
                if ($plain !== false) $rec = json_decode($plain, true);
            }
            if (!is_array($rec)) goal_out(['ok' => false, 'error' => 'The saved copy for this change is gone (kept ' . GOAL_UNDO_DAYS . ' days), so it can\'t be undone']);
            $info = goal_undo($pdo, $type, $rec, !empty($payload['force']));
            @unlink($path);
            goal_out(['ok' => true, 'result' => $info]);
        case 'schema':
            goal_out(['ok' => true, 'result' => goal_schema($pdo, $type)]);
        case 'query':
            $limit = max(1, min(1000, (int)(isset($payload['limit']) ? $payload['limit'] : 50)));
            list($cols, $rows) = goal_rows($pdo, $type, goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''), $limit);
            goal_out(['ok' => true, 'result' => ['columns' => $cols, 'rows' => $rows, 'row_count' => count($rows), 'limited' => count($rows) >= $limit]]);
        case 'export':
            goal_export_stream($pdo, $type, goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''));
        case 'explain':
            $sql = preg_replace('/^\s*explain\s+/i', '', goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''));
            list($cols, $rows) = goal_rows($pdo, $type, 'EXPLAIN ' . goal_safe_sql($sql));
            goal_out(['ok' => true, 'result' => ['columns' => $cols, 'rows' => $rows, 'row_count' => count($rows)]]);
        case 'health':
            goal_out(['ok' => true, 'result' => goal_health($pdo, $type)]);
        case 'live':
            goal_out(['ok' => true, 'result' => goal_live($pdo, $type)]);
        default:
            goal_out(['ok' => false, 'error' => 'Unknown job']);
    }
} catch (Exception $e) {
    goal_out(['ok' => false, 'error' => substr($e->getMessage(), 0, 300)]);
}
