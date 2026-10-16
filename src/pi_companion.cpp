#ifdef PI_COMPANION
#include <Arduino.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <RF24.h>
#include <BleKeyboard.h>
#include <esp_wifi.h>
#include <mbedtls/base64.h>
#include "app_config.h"
#include "input_manager.h"
#include "app_lifecycle.h"
#include "ui_theme.h"
#include "wifi_radar.h"
#include "wifi_channel_scan.h"
#include "spectrograph.h"
#include "rf_heatmap.h"
#include "channel_advisor.h"
#include "coex_view.h"
#include "dual_nrf_scope.h"
#include "nrf_link.h"
#include "nrf_chat.h"
#include "btscan.h"
#include "bt_analyzer.h"
#include "bt_spectrum.h"
#include "packet_monitor.h"
#include "centinela.h"
#include "games_menu.h"
#include "about_info.h"

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, 22, 21);
RF24 jam1(5, 17, 16000000), jam2(16, 4, 16000000);
int target_channel = 1;
bool runningApp = false;
static BleKeyboard mediaKeyboard("BWifiKill Media", "Pi Companion", 100);
static bool mediaActive = false;
bool btRemoteBleActive() { return mediaActive; }
void btRemoteMarkBleReleased() { mediaActive = false; }
static uint32_t bootId, sequence = 0, lastHost = 0, lastStatus = 0, lastFrame = 0;
static bool linked = false;
static uint32_t restartAt = 0;
static bool rf1ok = false, rf2ok = false;
static bool scanPending = false;
static uint32_t scanStart;
static int scanCount = 0, scanRow = 0;
struct BleObservation { char name[48]; char address[20]; int rssi; };
static QueueHandle_t bleQueue;

static String quote(const String& value) {
    String out = "\"";
    for (size_t i = 0; i < value.length(); i++) {
        unsigned char c = value[i];
        if (c == '\\' || c == '"') { out += '\\'; out += (char)c; }
        else if (c < 32) { char escaped[7]; snprintf(escaped, sizeof(escaped), "\\u%04x", c); out += escaped; }
        else out += (char)c;
    }
    return out + "\"";
}
static void event(const char* type, const String& data) {
    Serial.printf("{\"v\":1,\"boot\":%lu,\"seq\":%lu,\"uptime_ms\":%lu,\"type\":\"%s\",\"data\":%s}\n",
        (unsigned long)bootId, (unsigned long)++sequence, (unsigned long)millis(), type, data.c_str());
}
// BLE callbacks run on another task: transfer bounded records to the main loop.
void companionBleObservation(const char* name, const char* address, int rssi) {
    if (!bleQueue) return;
    BleObservation record{};
    strlcpy(record.name, name, sizeof(record.name));
    strlcpy(record.address, address, sizeof(record.address));
    record.rssi = rssi;
    xQueueSend(bleQueue, &record, 0);
}
static void status() {
    event("status", String("{\"app\":") + Host.currentIndex() + ",\"name\":" + quote(Host.currentName()) +
        ",\"heap\":" + ESP.getFreeHeap() + ",\"rf1\":" + (rf1ok ? "true" : "false") +
        ",\"rf2\":" + (rf2ok ? "true" : "false") + "}");
}
static void scanBegin() {
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false);
    WiFi.scanDelete();
    scanCount = 0; scanRow = 0;
    scanPending = WiFi.scanNetworks(true, true, false, 150) == WIFI_SCAN_RUNNING;
    scanStart = millis();
    event("wifi.scan", scanPending ? "{\"state\":\"scanning\"}" : "{\"state\":\"failed\"}");
}
static void scanExit() {
    esp_wifi_scan_stop();
    WiFi.scanDelete();
    WiFi.mode(WIFI_OFF);
    scanPending = false;
}
static void scanLoop() {
    if (scanPending) {
        int result = WiFi.scanComplete();
        if (result != WIFI_SCAN_RUNNING || millis() - scanStart > 12000) {
            scanPending = false;
            if (result == WIFI_SCAN_RUNNING) esp_wifi_scan_stop();
            scanCount = max(0, min(result, 30));
            for (int i = 0; i < scanCount; i++) {
                event("wifi.network", String("{\"ssid\":") + quote(WiFi.SSID(i)) + ",\"bssid\":" +
                    quote(WiFi.BSSIDstr(i)) + ",\"rssi\":" + WiFi.RSSI(i) + ",\"channel\":" +
                    WiFi.channel(i) + ",\"security\":" + (int)WiFi.encryptionType(i) + "}");
            }
            event("wifi.scan", String("{\"state\":\"") + (result >= 0 ? "complete" : "failed") + "\",\"count\":" + scanCount + "}");
        }
    }
    if (Input.pressed(BTN_ID_AUX) && !scanPending) scanBegin();
    if (scanCount) {
        if (Input.repeating(BTN_ID_UP)) scanRow = (scanRow + scanCount - 1) % scanCount;
        if (Input.repeating(BTN_ID_DOWN)) scanRow = (scanRow + 1) % scanCount;
    }
    u8g2.clearBuffer();
    UiTheme::drawHeader(u8g2, "WIFI SCAN", scanPending ? "SCAN" : "USB");
    u8g2.setFont(u8g2_font_5x7_tr);
    if (!scanCount) u8g2.drawStr(4, 35, scanPending ? "Scanning..." : "AUX: scan again");
    else {
        u8g2.drawStr(3, 30, WiFi.SSID(scanRow).substring(0, 23).c_str());
        u8g2.setCursor(3, 44); u8g2.printf("CH %d RSSI %d", WiFi.channel(scanRow), WiFi.RSSI(scanRow));
        u8g2.drawStr(3, 59, "UP/DOWN browse AUX scan");
    }
    u8g2.sendBuffer();
}
static void mediaEnter() {
    WiFi.mode(WIFI_OFF);
    mediaKeyboard.begin();
    mediaActive = true;
}
static void mediaExit() {
    mediaKeyboard.releaseAll();
    mediaKeyboard.end();
    mediaActive = false;
}
static void mediaLoop() {
    u8g2.clearBuffer();
    UiTheme::drawHeader(u8g2, "MEDIA REMOTE", mediaKeyboard.isConnected() ? "PAIRED" : "PAIR");
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(3, 30, "UP/DOWN: volume");
    u8g2.drawStr(3, 43, "OK: play/pause");
    u8g2.drawStr(3, 56, "AUX: next track");
    u8g2.sendBuffer();
    if (!mediaKeyboard.isConnected()) return;
    if (Input.repeating(BTN_ID_UP)) mediaKeyboard.write(KEY_MEDIA_VOLUME_UP);
    if (Input.repeating(BTN_ID_DOWN)) mediaKeyboard.write(KEY_MEDIA_VOLUME_DOWN);
    if (Input.pressed(BTN_ID_OK)) mediaKeyboard.write(KEY_MEDIA_PLAY_PAUSE);
    if (Input.pressed(BTN_ID_AUX)) mediaKeyboard.write(KEY_MEDIA_NEXT_TRACK);
}
#define NA {"Unavailable", nullptr, nullptr, nullptr}
static const App apps[] = {
    {"Wi-Fi Scanner", scanBegin, scanLoop, scanExit},
    {"Wi-Fi Radar", wifiRadarEnter, wifiRadarLoop, wifiRadarExit},
    {"Channel Scan", wifiChannelScanEnter, wifiChannelScanLoop, wifiChannelScanExit},
    {"RF Analyzer", spectrographEnter, spectrographLoop, spectrographExit},
    {"BLE Scanner", btscanSetup, btscanLoop, btscanExit},
    {"Packet Monitor", monitorEnter, monitorLoop, monitorExit},
    {"Sentinel", centinelaEnter, centinelaLoop, centinelaExit},
    NA, NA, NA, NA, NA, NA, NA, NA, NA, NA,
    {"Media Remote", mediaEnter, mediaLoop, mediaExit},
    NA,
    {"Arcade", gamesEnter, gamesLoop, gamesExit},
    {"About", aboutEnter, aboutLoop, aboutExit},
    {"BLE Analyzer", btAnalyzerEnter, btAnalyzerLoop, btAnalyzerExit},
    {"BT Spectrum", btSpectrumEnter, btSpectrumLoop, btSpectrumExit},
    {"RF Heatmap", rfHeatmapEnter, rfHeatmapLoop, rfHeatmapExit},
    {"Channel Advisor", channelAdvisorEnter, channelAdvisorLoop, channelAdvisorExit},
    {"nRF Link", nrfLinkEnter, nrfLinkLoop, nrfLinkExit},
    {"nRF Chat", nrfChatEnter, nrfChatLoop, nrfChatExit},
    {"Coexistence", coexViewEnter, coexViewLoop, coexViewExit},
    {"Dual nRF Scope", dualNrfScopeEnter, dualNrfScopeLoop, dualNrfScopeExit}, NA
};
static void stopApp() {
    int previous = Host.currentIndex();
    Host.shutdown();
    esp_wifi_set_promiscuous(false);
    WiFi.mode(WIFI_OFF);
    jam1.stopListening(); jam2.stopListening();
    jam1.powerDown(); jam2.powerDown();
    runningApp = false;
    Input.resetAll();
    // ESP32 BLE Keyboard::end() is a no-op in v0.3.2. Restart after
    // releasing BLE tools rather than retaining invalid service pointers.
    if (previous == 4 || previous == 17 || previous == 21 || previous == 22)
        restartAt = millis() + 250;
}
static void ack(unsigned long id, const char* result) {
    event("ack", String("{\"id\":") + id + ",\"status\":" + quote(result) + "}");
}
static void command(char* line) {
    unsigned long id;
    char verb[16] = {}, arg[20] = {}, extra[20] = {}, tail[2] = {};
    int fields = sscanf(line, "%lu %15s %19s %19s %1s", &id, verb, arg, extra, tail);
    if (fields < 2) return;
    lastHost = millis();
    if (!strcmp(verb, "HELLO") && fields == 2) {
        linked = true;
        event("hello", "{\"protocol\":1,\"firmware\":\"pi-companion-1\",\"apps\":[0,1,2,3,4,5,6,17,19,20,21,22,23,24,25,26,27,28]}");
    } else if (!linked) { ack(id, "handshake_required"); return; }
    else if (!strcmp(verb, "STATUS") && fields == 2) { status(); }
    else if (!strcmp(verb, "STOP") && fields == 2) { stopApp(); status(); }
    else if (!strcmp(verb, "START") && fields == 3) {
        if (restartAt) { ack(id, "restarting"); return; }
        char* end; long index = strtol(arg, &end, 10);
        if (*end || index < 0 || index >= 30 || !apps[index].loop) { ack(id, "unsupported"); return; }
        if (Host.isRunning()) { ack(id, "busy"); return; }
        // BLE stack recycling differs across Arduino versions. A clean boot
        // before changing BLE modes keeps repeated sessions predictable.
        runningApp = true;
        Input.resetAll();
        Host.launch(index);
        status();
    } else if (!strcmp(verb, "KEY") && fields == 4) {
        const char* names[] = {"UP", "DOWN", "OK", "BACK", "AUX"};
        int key = -1;
        for (int i = 0; i < BTN_COUNT; i++) if (!strcmp(names[i], arg)) key = i;
        if (key < 0 || (strcmp(extra, "0") && strcmp(extra, "1"))) { ack(id, "invalid"); return; }
        Input.remoteKey((ButtonId)key, extra[0] == '1');
    } else { ack(id, "invalid"); return; }
    ack(id, "ok");
}
static void receive() {
    static char line[96]; static size_t used = 0; static bool overflow = false;
    for (int budget = 0; budget < 256 && Serial.available(); budget++) {
        char c = Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            if (!overflow) { line[used] = 0; command(line); }
            used = 0; overflow = false;
        } else if (used < sizeof(line)-1 && !overflow) line[used++] = c;
        else overflow = true;
    }
}
void setup() {
    Serial.begin(115200);
    bootId = esp_random();
    bleQueue = xQueueCreate(24, sizeof(BleObservation));
    u8g2.begin(); Input.begin(); Host.registerApps(apps, 30);
    rf1ok = jam1.begin() && jam1.isChipConnected();
    rf2ok = jam2.begin() && jam2.isChipConnected();
    event("boot", "{\"firmware\":\"pi-companion-1\"}");
}
void loop() {
    if (restartAt && (int32_t)(millis() - restartAt) >= 0) { Serial.flush(); ESP.restart(); }
    receive();
    Input.update();
    if (linked && millis() - lastHost > 7000) {
        stopApp(); linked = false;
        event("link.timeout", "{\"message\":\"Controller disconnected; tool stopped\"}");
    }
    if (Host.isRunning()) {
        Host.tick();
        if (Input.pressed(BTN_ID_BACK)) { stopApp(); status(); }
    } else {
        u8g2.clearBuffer(); UiTheme::drawHeader(u8g2, "PI COMPANION", linked ? "USB" : "WAIT");
        u8g2.setFont(u8g2_font_5x7_tr);
        u8g2.drawStr(5, 33, linked ? "Choose a tool on Pi" : "Connect Pi by USB");
        u8g2.drawStr(5, 50, "OLED + remote buttons"); u8g2.sendBuffer();
    }
    BleObservation ble;
    for (int i = 0; i < 4 && xQueueReceive(bleQueue, &ble, 0) == pdTRUE; i++) {
        event("ble.device", String("{\"name\":") + quote(ble.name) + ",\"address\":" + quote(ble.address) + ",\"rssi\":" + ble.rssi + "}");
    }
    if (linked && millis() - lastStatus >= 1000) { lastStatus = millis(); status(); }
    if (linked && millis() - lastFrame >= 250) {
        lastFrame = millis();
        unsigned char encoded[1370]; size_t length = 0;
        if (!mbedtls_base64_encode(encoded, sizeof(encoded), &length, u8g2.getBufferPtr(), 1024)) {
            encoded[length] = 0;
            event("frame", String("{\"width\":128,\"height\":64,\"format\":\"u8g2-vlsb\",\"base64\":\"") + (char*)encoded + "\"}");
        }
    }
    delay(5);
}
#endif