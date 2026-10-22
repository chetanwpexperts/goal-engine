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
define('GOAL_KEY', '__KEY__');
define('GOAL_VERSION', '1.0');
define('GOAL_MAX_ROWS', 100000);
define('GOAL_MAX_BYTES', 10 * 1024 * 1024);

@ini_set('display_errors', '0');
@set_time_limit(120);
header('Content-Type: application/json; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');
header('Cache-Control: no-store');

$GOAL_ENCRYPT = false;

// Requests and answers are encrypted with this file's key (AES-256-GCM), so firewalls see no SQL
// and nobody in between can read your data.
function goal_aes_key() { return hash('sha256', 'goal-engine-transport:' . GOAL_KEY, true); }

function goal_out($data, $code = 200) {
    global $GOAL_ENCRYPT;
    http_response_code($code);
    $json = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR);
    if ($GOAL_ENCRYPT) {
        $iv = random_bytes(12);
        $tag = '';
        $ct = openssl_encrypt($json, 'aes-256-gcm', goal_aes_key(), OPENSSL_RAW_DATA, $iv, $tag);
        $json = json_encode(['ok' => true, 'sealed' => base64_encode($iv . $ct . $tag)]);
    }
    echo $json;
    exit;
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
    $pdo = new PDO($dsn, $c['user'], $c['password'], [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 10,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_NUM, PDO::ATTR_EMULATE_PREPARES => false]);
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
        while (($r = $st->fetch()) !== false) {
            $rows[] = array_map('goal_clean', $r);
            if ($limit && count($rows) >= $limit) break;
        }
        $st->closeCursor();
    } finally {
        try { $pdo->exec('ROLLBACK'); } catch (Exception $e) {}
    }
    return [$cols, $rows];
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

function goal_export($pdo, $type, $sql) {
    goal_begin($pdo, $type);
    $fh = fopen('php://temp', 'w+');
    fwrite($fh, "\xEF\xBB\xBF");
    $count = 0;
    $limited = false;
    try {
        $st = $pdo->query($sql);
        $cols = [];
        for ($i = 0; $i < $st->columnCount(); $i++) { $m = $st->getColumnMeta($i); $cols[] = $m['name']; }
        fputcsv($fh, $cols);
        while (($r = $st->fetch()) !== false) {
            fputcsv($fh, array_map('goal_clean', $r));
            $count++;
            if ($count >= GOAL_MAX_ROWS || ftell($fh) > GOAL_MAX_BYTES) { $limited = true; break; }
        }
        $st->closeCursor();
    } finally {
        try { $pdo->exec('ROLLBACK'); } catch (Exception $e) {}
    }
    rewind($fh);
    $csv = stream_get_contents($fh);
    fclose($fh);
    return [['row_count' => $count, 'limited' => $limited], $csv];
}

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
                'db_name' => $cfg['database'], 'db_version' => goal_version($pdo, $type), 'can_write' => goal_can_write($pdo, $type), 'app' => $cfg['app']]]);
        case 'schema':
            goal_out(['ok' => true, 'result' => goal_schema($pdo, $type)]);
        case 'query':
            $limit = max(1, min(1000, (int)(isset($payload['limit']) ? $payload['limit'] : 50)));
            list($cols, $rows) = goal_rows($pdo, $type, goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''), $limit);
            goal_out(['ok' => true, 'result' => ['columns' => $cols, 'rows' => $rows, 'row_count' => count($rows), 'limited' => count($rows) >= $limit]]);
        case 'export':
            list($res, $csv) = goal_export($pdo, $type, goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''));
            goal_out(['ok' => true, 'result' => $res, 'csv' => $csv]);
        case 'explain':
            $sql = preg_replace('/^\s*explain\s+/i', '', goal_safe_sql(isset($payload['sql']) ? $payload['sql'] : ''));
            list($cols, $rows) = goal_rows($pdo, $type, 'EXPLAIN ' . goal_safe_sql($sql));
            goal_out(['ok' => true, 'result' => ['columns' => $cols, 'rows' => $rows, 'row_count' => count($rows)]]);
        case 'health':
            goal_out(['ok' => true, 'result' => goal_health($pdo, $type)]);
        default:
            goal_out(['ok' => false, 'error' => 'Unknown job']);
    }
} catch (Exception $e) {
    goal_out(['ok' => false, 'error' => substr($e->getMessage(), 0, 300)]);
}
