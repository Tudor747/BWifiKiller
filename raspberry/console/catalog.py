"""Stable IDs match the original ESP32 app table. No raw command passthrough."""

NAMES = ["Wi-Fi Scanner", "Wi-Fi Radar", "Channel Scan", "RF Analyzer",
         "BLE Scanner", "Packet Monitor", "Sentinel", "Channel Jammer",
         "Full Sweep", "BT Jammer", "Beacon Spam", "BLE Spam", "Hybrid Mode",
         "Evil Portal", "IP Scanner", "Slave Control", "Web Dashboard",
         "BT Remote", "View Logs", "Arcade", "About", "BLE Analyzer",
         "BT Spectrum", "RF Heatmap", "Channel Advisor", "nRF Link",
         "nRF Chat", "BT/Wi-Fi Coexistence", "Dual nRF Scope", "Deauther"]
GROUPS = {"Wi-Fi": [0, 1, 2, 5, 6, 14, 16], "RF": [3, 23, 24, 25, 26, 27, 28],
          "Bluetooth": [4, 21, 22, 17], "Restricted inventory": [7, 8, 9, 10, 11, 12, 13, 29],
          "Games": [19], "System": [15, 18, 20]}
SUPPORTED = {0, 1, 2, 3, 4, 5, 6, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28}
NOTES = {
    0: "Structured Wi-Fi results and OLED view; AUX rescans.",
    14: "Use the Pi Network discovery test.",
    15: "Legacy slave protocol is not implemented in the companion firmware.",
    16: "Opens the combined Pi dashboard; data arrives over USB.",
    17: "Paired media remote only: volume, play/pause, next track.",
    18: "Opens Pi diagnostic logs; portal submissions are not collected.",
}


def catalog():
    return [{"id": i, "name": name, "group": next(k for k, v in GROUPS.items() if i in v),
             "supported": i in SUPPORTED, "route": {14: "discovery", 16: "dashboard", 18: "logs"}.get(i),
             "note": NOTES.get(i, "OLED view and remote controls." if i in SUPPORTED else
                               "Listed for reference; this build does not implement remote operation.")}
            for i, name in enumerate(NAMES)]


TESTS = [
    {"id": "ping", "name": "Reachability", "team": "blue", "description": "Three ICMP echo requests."},
    {"id": "tcp", "name": "TCP services", "team": "blue", "description": "Connection status and latency on selected ports."},
    {"id": "dns", "name": "DNS response", "team": "blue", "description": "A protocol-valid UDP DNS query to a selected resolver."},
    {"id": "http", "name": "HTTP health", "team": "blue", "description": "Status and response latency; redirects are reported, not followed."},
    {"id": "tls", "name": "TLS certificate", "team": "blue", "description": "Validate a certificate and inspect its expiry."},
    {"id": "udp_echo", "name": "UDP loss / jitter", "team": "blue", "description": "Bounded requests to the supplied echo receiver."},
    {"id": "discovery", "name": "Network discovery", "team": "red", "description": "Check up to 32 scoped hosts on selected TCP ports."},
    {"id": "services", "name": "Service inspection", "team": "red", "description": "Connect to selected ports and read a bounded greeting."},
    {"id": "segmentation", "name": "Segmentation check", "team": "red", "description": "Compare TCP reachability with an expected allow/deny rule."},
    {"id": "tcp_echo", "name": "TCP bounded test", "team": "red", "description": "Paced echo messages to your cooperating receiver."},
    {"id": "udp_echo", "name": "UDP bounded test", "team": "red", "description": "At most 100 small messages, at most 10 per second."},
]
