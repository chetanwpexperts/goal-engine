<?php
$base = rtrim(getenv('DBABA_URL') ?: 'http://127.0.0.1:8000', '/');
$key = getenv('DBABA_API_KEY');
if (!$key) {
    fwrite(STDERR, "Set DBABA_API_KEY in your environment.\n");
    exit(1);
}
$query = $argv[1] ?? 'SELECT * FROM orders WHERE customer_id = 42;';
$ch = curl_init();
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_COOKIEFILE => '',
    CURLOPT_POST => true,
    CURLOPT_TIMEOUT => 120,
]);

function post($ch, $url, $key, $body = null) {
    curl_setopt_array($ch, [
        CURLOPT_URL => $url,
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . $key,
            'Content-Type: application/json',
        ],
        CURLOPT_POSTFIELDS => $body === null ? '' : json_encode($body, JSON_THROW_ON_ERROR),
    ]);
    $response = curl_exec($ch);
    if ($response === false) {
        throw new RuntimeException(curl_error($ch));
    }
    $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    if ($status < 200 || $status >= 300) {
        throw new RuntimeException("HTTP $status: $response");
    }
    return json_decode($response, true, 512, JSON_THROW_ON_ERROR);
}

try {
    post($ch, $base . '/api/new-chat', $key);
    $result = post($ch, $base . '/api/message', $key, ['text' => $query]);
    echo $result['reply'] . PHP_EOL;
} catch (Throwable $error) {
    fwrite(STDERR, $error->getMessage() . PHP_EOL);
    exit(1);
} finally {
    curl_close($ch);
}
