<?php
/**
 * Liquidctl Plugin API
 * GET  api.php?action=status        → current status + history JSON
 * GET  api.php?action=settings      → current settings JSON
 * POST api.php?action=settings      → save settings body, SIGHUP daemon
 * GET  api.php?action=daemon        → daemon running status
 * POST api.php?action=service       → {"cmd":"start|stop|restart|reload"}
 * GET  api.php?action=log&lines=N   → last N lines of daemon log
 * GET  api.php?action=devices       → list detected liquidctl devices
 */

define('PLUGIN_NAME',   'liquidctl');
define('STATUS_FILE',   '/var/run/' . PLUGIN_NAME . '/status.json');
define('SETTINGS_FILE', '/boot/config/plugins/' . PLUGIN_NAME . '/settings.json');
define('PID_FILE',      '/var/run/' . PLUGIN_NAME . '/daemon.pid');
define('LOG_FILE',      '/var/log/' . PLUGIN_NAME . '.log');
define('RC_SCRIPT',     '/usr/local/sbin/rc.' . PLUGIN_NAME);
define('VENV_LCTL',     '/boot/config/plugins/' . PLUGIN_NAME . '/venv/bin/liquidctl');

header('Content-Type: application/json');
header('Cache-Control: no-store');

$DEFAULTS = [
    'device_match'         => '',
    'pump_mode'            => 'Balanced',
    'pump_mode_supported'  => true,
    'fan1_curve'           => [[25,30],[35,50],[45,80],[50,100]],
    'fan2_curve'           => [[25,30],[35,50],[45,80],[50,100]],
    'fan1_channel'         => 'fan1',
    'fan2_channel'         => 'fan2',
    'pump_channel'         => '',
    'hysteresis'           => 2.0,
];

function daemon_pid(): int {
    if (!file_exists(PID_FILE)) return 0;
    $pid = (int)trim(file_get_contents(PID_FILE));
    return ($pid > 0 && posix_kill($pid, 0)) ? $pid : 0;
}

function json_out($data): void {
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
}

$action = $_GET['action'] ?? 'status';
$method = $_SERVER['REQUEST_METHOD'];

switch ($action) {

    case 'status':
        if (file_exists(STATUS_FILE)) {
            echo file_get_contents(STATUS_FILE);
        } else {
            json_out(['error' => 'No status data yet — is the daemon running?']);
        }
        break;

    case 'settings':
        global $DEFAULTS;
        if ($method === 'POST') {
            $raw  = file_get_contents('php://input');
            $data = json_decode($raw, true);
            if (!is_array($data)) {
                http_response_code(400);
                json_out(['error' => 'Invalid JSON body']);
                break;
            }
            $merged = array_merge($DEFAULTS, $data);
            @mkdir(dirname(SETTINGS_FILE), 0755, true);
            file_put_contents(SETTINGS_FILE, json_encode($merged, JSON_PRETTY_PRINT));
            $pid = daemon_pid();
            if ($pid) posix_kill($pid, SIGHUP);
            json_out(['ok' => true, 'daemon_reloaded' => (bool)$pid]);
        } else {
            if (file_exists(SETTINGS_FILE)) {
                $s = json_decode(file_get_contents(SETTINGS_FILE), true) ?? [];
                json_out(array_merge($DEFAULTS, $s));
            } else {
                json_out($DEFAULTS);
            }
        }
        break;

    case 'daemon':
        $pid = daemon_pid();
        json_out(['running' => (bool)$pid, 'pid' => $pid]);
        break;

    case 'service':
        if ($method !== 'POST') { http_response_code(405); break; }
        $body = json_decode(file_get_contents('php://input'), true);
        $cmd  = $body['cmd'] ?? '';
        if (!in_array($cmd, ['start', 'stop', 'restart', 'reload'], true)) {
            http_response_code(400);
            json_out(['error' => 'Invalid command']);
            break;
        }
        $out = shell_exec(RC_SCRIPT . " $cmd 2>&1");
        json_out(['ok' => true, 'output' => trim($out ?? '')]);
        break;

    case 'log':
        $n = max(1, min(500, (int)($_GET['lines'] ?? 100)));
        $lines = [];
        if (file_exists(LOG_FILE)) {
            $all   = file(LOG_FILE, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
            $lines = array_slice($all, -$n);
        }
        json_out(['lines' => $lines]);
        break;

    case 'devices':
        // Probe what liquidctl can see
        if (!file_exists(VENV_LCTL)) {
            json_out(['error' => 'liquidctl not installed']);
            break;
        }
        $out = shell_exec(escapeshellarg(VENV_LCTL) . ' list 2>&1');
        $devices = [];
        foreach (explode("\n", $out ?? '') as $line) {
            if (preg_match('/^Device #(\d+):\s*(.+)$/', trim($line), $m)) {
                $devices[] = ['index' => (int)$m[1], 'name' => trim($m[2])];
            }
        }
        json_out(['devices' => $devices]);
        break;

    default:
        http_response_code(404);
        json_out(['error' => "Unknown action: $action"]);
}
