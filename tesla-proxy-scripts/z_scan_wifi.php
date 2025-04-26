<?php
$interface = 'wlx7ca7b0bea5a2'; // Sostituisci con la tua interfaccia di rete Wi-Fi
$mac_target = 'ec:64:c9:c6:bb:08';

$command = escapeshellcmd("sudo arp-scan --interface=$interface --localnet");
$output = shell_exec($command);
echo $output;

$lines = explode("\n", $output);
#print_r($lines);
foreach ($lines as $line) {
    if (stripos($line, $mac_target) !== false) {
        $parts = preg_split('/\s+/', trim($line));
        $ip = $parts[0];
        echo $ip;
        exit;
    }
}
echo "Dispositivo Shelly non trovato sulla rete.\n";
?>
