<?php

echo "✅ Avvio script\n";
echo "SAPI: " . php_sapi_name() . "\n";

if (php_sapi_name() !== 'cli') {
    echo "Questo script può essere eseguito solo da riga di comando.\n";
    exit(1);
}

// Parametri statici
$VIN = "LRW3E7FA9MC345603";
$token_file = "/home/sergio/Scrivania/docker/shelly_monitoring/flask/data/tesla_token_latest.json";
$cert_path = "/home/sergio/Scrivania/docker/shelly_monitoring/tesla-proxy-config/cert.pem";

// Parametri da linea di comando
$command_param = $argv[1] ?? null;
$value_param = $argv[2] ?? null;

if (!$command_param) {
    echo json_encode(["status" => "error", "message" => "Comando mancante"]);
    exit(1);
}

// Leggi il token
$json = @file_get_contents($token_file);
$data = json_decode($json, true);
$access_token = $data["access_token"] ?? null;

if (!$access_token) {
    echo json_encode(["status" => "error", "message" => "Token non trovato"]);
    exit(1);
}

// Prepara parametri curl
$cert_arg = escapeshellarg($cert_path);
$auth_header = escapeshellarg("Authorization: Bearer $access_token");
$url_arg = escapeshellarg("https://localhost:4443/api/1/vehicles/$VIN/command/$command_param");

// Prepara JSON per --data
if ($command_param === "set_charging_amps" && $value_param !== null) {
    $json_data = escapeshellarg(json_encode(["charging_amps" => (int)$value_param]));
} else {
    $json_data = escapeshellarg("{}");
}

$cmd = "curl --cacert $cert_arg " .
       "--header 'Content-Type: application/json' " .
       "--header $auth_header " .
       "--data $json_data $url_arg";

// Esegui il comando
exec($cmd, $output, $return_var);

// Output risultato
echo json_encode([
    "status" => $return_var === 0 ? "success" : "error",
    "command_sent" => $command_param,
    "value" => $value_param,
    "output" => $output,
    "code" => $return_var
], JSON_PRETTY_PRINT);
