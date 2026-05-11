#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <memory>

#include "cJSON.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "driver/spi_master.h"
#include "driver/temperature_sensor.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_lcd_ili9341.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mqtt_client.h"
#include "nvs_flash.h"
#include "es7210_adc.h"
#include "SCSCL.h"

namespace {

constexpr char kTag[] = "Hermes2StackChan";
constexpr int kWidth = 320;
constexpr int kHeight = 240;
constexpr int kWifiConnectedBit = BIT0;
constexpr gpio_num_t kI2cSda = GPIO_NUM_12;
constexpr gpio_num_t kI2cScl = GPIO_NUM_11;
constexpr uint8_t kPmicAddr = 0x34;
constexpr uint8_t kAw9523Addr = 0x58;
constexpr uint8_t kPy32Addr = 0x6F;
constexpr uint8_t kHeadTouchAddr = 0x68;
constexpr uint8_t kAw88298Addr = AW88298_CODEC_DEFAULT_ADDR;
constexpr uint8_t kEs7210Addr = ES7210_CODEC_DEFAULT_ADDR;
constexpr uint16_t kBlack = 0x0000;
constexpr size_t kFrameBufferBytes = kWidth * kHeight * sizeof(uint16_t);
constexpr int kAudioSampleRate = 16000;
constexpr size_t kWavHeaderBytes = 44;
constexpr int kDefaultIdleYawPct = 0;
constexpr int kDefaultIdlePitchPct = 45;
constexpr int kYawTargetMinPct = -100;
constexpr int kYawTargetMaxPct = 100;
constexpr int kPitchTargetMinPct = 0;
constexpr int kPitchTargetMaxPct = 100;
constexpr int kVoiceStartGraceMs = 250;
constexpr int kVoiceNoSpeechTimeoutMs = 5000;
constexpr int kVoiceMinSpeechMs = 250;
constexpr int kVoiceSilenceAvgThreshold = 260;
constexpr int kVoiceSilencePeakThreshold = 900;
constexpr int kDefaultSpeakerVolumePct = 80;
constexpr int kMaxMqttTopic = 128;
constexpr int kMaxMqttPayload = 4096;
constexpr gpio_num_t kAudioMclk = GPIO_NUM_0;
constexpr gpio_num_t kAudioBclk = GPIO_NUM_34;
constexpr gpio_num_t kAudioWs = GPIO_NUM_33;
constexpr gpio_num_t kAudioDin = GPIO_NUM_14;
constexpr gpio_num_t kAudioDout = GPIO_NUM_13;

esp_lcd_panel_handle_t g_panel = nullptr;
esp_lcd_panel_io_handle_t g_panel_io = nullptr;
uint16_t* g_framebuffer = nullptr;
bool g_framebuffer_active = false;
SemaphoreHandle_t g_display_mutex = nullptr;
i2c_master_bus_handle_t g_i2c_bus = nullptr;
SemaphoreHandle_t g_i2c_mutex = nullptr;
EventGroupHandle_t g_wifi_events = nullptr;
esp_mqtt_client_handle_t g_mqtt_client = nullptr;
bool g_mqtt_connected = false;
uint8_t g_display_brightness_pct = CONFIG_STACKCHAN_DISPLAY_BRIGHTNESS;
bool g_display_sleeping = false;
SCSCL g_servo_bus;
i2s_chan_handle_t g_audio_tx = nullptr;
i2s_chan_handle_t g_audio_rx = nullptr;
const audio_codec_data_if_t* g_audio_data_if = nullptr;
const audio_codec_ctrl_if_t* g_audio_in_ctrl_if = nullptr;
const audio_codec_if_t* g_audio_in_codec_if = nullptr;
esp_codec_dev_handle_t g_audio_input = nullptr;
const audio_codec_ctrl_if_t* g_audio_out_ctrl_if = nullptr;
const audio_codec_gpio_if_t* g_audio_gpio_if = nullptr;
const audio_codec_if_t* g_audio_out_codec_if = nullptr;
esp_codec_dev_handle_t g_audio_output = nullptr;
bool g_audio_output_ready = false;
int g_speaker_volume_pct = kDefaultSpeakerVolumePct;
temperature_sensor_handle_t g_temperature_sensor = nullptr;
bool g_temperature_sensor_ready = false;
volatile int g_temperature_soc_c = -1;
volatile int g_temperature_servo_yaw_c = -1;
volatile int g_temperature_servo_pitch_c = -1;
volatile int g_battery_pct = -1;
volatile bool g_battery_charging = false;
volatile bool g_battery_discharging = false;
volatile bool g_battery_charging_done = false;
volatile bool g_battery_known = false;
volatile bool g_usb_power_present = false;
volatile int g_battery_current_direction = -1;
volatile int g_led_mode = 0;
volatile int g_led_r = 0;
volatile int g_led_g = 0;
volatile int g_led_b = 0;
bool g_neon_ready = false;
volatile int g_servo_yaw_pct = 0;
volatile int g_servo_pitch_pct = 0;
volatile bool g_servo_ready = false;
volatile int g_pending_yaw_delta = 0;
volatile int g_pending_pitch_delta = 0;
volatile int g_pending_yaw_target_pct = 101;
volatile int g_pending_pitch_target_pct = 101;
char g_face_emotion[24] = "neutral";
int g_face_intensity_pct = 60;
char g_pre_recording_face_emotion[24] = "neutral";
int g_pre_recording_face_intensity_pct = 60;
volatile bool g_audio_input_ready = false;
volatile bool g_wakeword_enabled = false;
volatile bool g_recording = false;
volatile bool g_head_touch_ready = false;
volatile bool g_display_touch_ready = false;
volatile bool g_touch_pressed = false;
volatile int g_touch_raw = 0;
volatile int g_touch_x = -1;
volatile int g_touch_y = -1;
volatile int64_t g_recording_started_ms = 0;
volatile int g_recording_min_ms = 5000;
volatile int g_recording_silence_timeout_ms = 500;
volatile int g_recording_max_ms = 15000;
volatile int g_voice_level_pct = 0;
volatile int g_voice_avg_level = 0;
volatile int g_voice_peak_level = 0;
volatile bool g_voice_active = false;
std::array<uint8_t, 64> g_voice_waveform = {};
volatile int g_voice_waveform_head = 0;
char g_wakeword[32] = "Computer";
char g_recording_source[24] = "none";

char g_topic_display[96] = {};
char g_topic_system[96] = {};
char g_topic_face[96] = {};
char g_topic_move[96] = {};
char g_topic_motion[96] = {};
char g_topic_sound[96] = {};
char g_topic_audio[96] = {};
char g_topic_led[96] = {};
char g_topic_device[96] = {};
char g_topic_say[96] = {};
char g_topic_status[96] = {};
char g_topic_ack[96] = {};
char g_topic_error[96] = {};
char g_topic_events[96] = {};
char g_ui_mode[16] = "face";
char g_mqtt_rx_topic[kMaxMqttTopic] = {};
char g_mqtt_rx_payload[kMaxMqttPayload + 1] = {};
int g_mqtt_rx_expected_len = 0;
int g_mqtt_rx_received_len = 0;

struct SoundCommand {
    int frequency_hz;
    int duration_ms;
    int volume_pct;
};

struct MotionPoint {
    int yaw_pct;
    int pitch_pct;
    int duration_ms;
    int speed_pct;
    int hold_ms;
};

struct MotionCommand {
    int point_count;
    int curve;
    MotionPoint points[48];
};

struct HttpResponseBuffer {
    char data[2048] = {};
    int len = 0;
    bool truncated = false;
};

struct WavPlaybackState {
    uint8_t header[256] = {};
    int header_len = 0;
    bool data_started = false;
    uint8_t pending_byte = 0;
    bool has_pending_byte = false;
};

enum class UiCommandType : uint8_t {
    Display,
    Face,
    Say,
};

struct UiCommand {
    UiCommandType type;
    char text[768];
    char emotion[24];
    int intensity_pct;
    int duration_ms;
    bool beep;
    uint16_t accent;
};

QueueHandle_t g_sound_queue = nullptr;
QueueHandle_t g_motion_queue = nullptr;
QueueHandle_t g_ui_queue = nullptr;

enum class FaceExtraMode : uint8_t {
    None,
    VoiceWaveform,
};

volatile FaceExtraMode g_face_extra_mode = FaceExtraMode::None;

bool wait_for_wifi(TickType_t timeout);
bool play_wav_url(const char* url);

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

int clamp_int(int value, int min_value, int max_value)
{
    return std::max(min_value, std::min(max_value, value));
}

void write_le16(uint8_t* out, uint16_t value)
{
    out[0] = static_cast<uint8_t>(value & 0xff);
    out[1] = static_cast<uint8_t>((value >> 8) & 0xff);
}

void write_le32(uint8_t* out, uint32_t value)
{
    out[0] = static_cast<uint8_t>(value & 0xff);
    out[1] = static_cast<uint8_t>((value >> 8) & 0xff);
    out[2] = static_cast<uint8_t>((value >> 16) & 0xff);
    out[3] = static_cast<uint8_t>((value >> 24) & 0xff);
}

void make_wav_header(uint8_t* out, uint32_t pcm_bytes)
{
    std::memcpy(out + 0, "RIFF", 4);
    write_le32(out + 4, 36 + pcm_bytes);
    std::memcpy(out + 8, "WAVEfmt ", 8);
    write_le32(out + 16, 16);
    write_le16(out + 20, 1);
    write_le16(out + 22, 1);
    write_le32(out + 24, kAudioSampleRate);
    write_le32(out + 28, kAudioSampleRate * 2);
    write_le16(out + 32, 2);
    write_le16(out + 34, 16);
    std::memcpy(out + 36, "data", 4);
    write_le32(out + 40, pcm_bytes);
}

int sanitize_temperature_c(int value)
{
    if (value < -40 || value > 125) {
        return -1;
    }
    return value;
}

void init_temperature_sensor()
{
    temperature_sensor_config_t config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(10, 80);
    esp_err_t err = temperature_sensor_install(&config, &g_temperature_sensor);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "temperature sensor install failed: %s", esp_err_to_name(err));
        g_temperature_sensor = nullptr;
        return;
    }
    err = temperature_sensor_enable(g_temperature_sensor);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "temperature sensor enable failed: %s", esp_err_to_name(err));
        g_temperature_sensor = nullptr;
        return;
    }
    g_temperature_sensor_ready = true;
}

void update_soc_temperature()
{
    if (!g_temperature_sensor_ready || !g_temperature_sensor) {
        return;
    }
    float celsius = 0.0f;
    if (temperature_sensor_get_celsius(g_temperature_sensor, &celsius) == ESP_OK) {
        g_temperature_soc_c = sanitize_temperature_c(static_cast<int>(std::round(celsius)));
    }
}

class I2cDevice {
public:
    I2cDevice(i2c_master_bus_handle_t bus, uint8_t address, uint32_t speed_hz = 400 * 1000)
    {
        i2c_device_config_t config = {};
        config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
        config.device_address = address;
        config.scl_speed_hz = speed_hz;
        ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &config, &device_));
    }

    void write_reg(uint8_t reg, uint8_t value)
    {
        ESP_ERROR_CHECK(try_write_reg(reg, value));
    }

    esp_err_t try_write_reg(uint8_t reg, uint8_t value)
    {
        uint8_t data[2] = {reg, value};
        return transact([&]() {
            return i2c_master_transmit(device_, data, sizeof(data), 100);
        });
    }

    esp_err_t try_write(uint8_t reg, const uint8_t* data, size_t len)
    {
        std::array<uint8_t, 16> buffer = {};
        if (len + 1 > buffer.size()) {
            return ESP_ERR_INVALID_SIZE;
        }
        buffer[0] = reg;
        std::memcpy(buffer.data() + 1, data, len);
        return transact([&]() {
            return i2c_master_transmit(device_, buffer.data(), len + 1, 100);
        });
    }

    esp_err_t try_read_reg(uint8_t reg, uint8_t& value)
    {
        return transact([&]() {
            return i2c_master_transmit_receive(device_, &reg, 1, &value, 1, 100);
        });
    }

    esp_err_t try_read(uint8_t reg, uint8_t* data, size_t len)
    {
        return transact([&]() {
            return i2c_master_transmit_receive(device_, &reg, 1, data, len, 100);
        });
    }

private:
    template <typename Operation>
    esp_err_t transact(Operation operation)
    {
        if (g_i2c_mutex) {
            if (xSemaphoreTake(g_i2c_mutex, pdMS_TO_TICKS(250)) != pdTRUE) {
                return ESP_ERR_TIMEOUT;
            }
            const esp_err_t err = operation();
            xSemaphoreGive(g_i2c_mutex);
            return err;
        }
        return operation();
    }

    i2c_master_dev_handle_t device_ = nullptr;
};

std::unique_ptr<I2cDevice> g_pmic;
std::unique_ptr<I2cDevice> g_py32;
std::unique_ptr<I2cDevice> g_head_touch;
std::unique_ptr<I2cDevice> g_display_touch;

bool init_head_touch()
{
    if (!g_i2c_bus) {
        return false;
    }
    const esp_err_t probe = i2c_master_probe(g_i2c_bus, kHeadTouchAddr, 120);
    if (probe != ESP_OK) {
        ESP_LOGW(kTag, "SI12T head touch not found at 0x68: %s", esp_err_to_name(probe));
        g_head_touch_ready = false;
        return false;
    }

    g_head_touch = std::make_unique<I2cDevice>(g_i2c_bus, kHeadTouchAddr, 100 * 1000);
    I2cDevice& dev = *g_head_touch;

    bool ok = true;
    for (uint8_t reg = 0x0A; reg <= 0x0F; ++reg) {
        ok = (dev.try_write_reg(reg, 0x00) == ESP_OK) && ok;
    }
    for (uint8_t reg = 0x02; reg <= 0x06; ++reg) {
        ok = (dev.try_write_reg(reg, 0x33) == ESP_OK) && ok;
    }
    ok = (dev.try_write_reg(0x09, 0x0F) == ESP_OK) && ok;
    ok = (dev.try_write_reg(0x09, 0x07) == ESP_OK) && ok;
    ok = (dev.try_write_reg(0x08, 0x22) == ESP_OK) && ok;
    if (!ok) {
        ESP_LOGW(kTag, "SI12T head touch setup failed");
        g_head_touch.reset();
        g_head_touch_ready = false;
        return false;
    }

    g_head_touch_ready = true;
    ESP_LOGI(kTag, "SI12T head touch ready");
    return true;
}

bool read_head_touch_pressed(uint8_t* raw_out = nullptr)
{
    if (!g_head_touch_ready || !g_head_touch) {
        return false;
    }
    uint8_t raw = 0;
    if (g_head_touch->try_read_reg(0x10, raw) != ESP_OK) {
        return false;
    }
    if (raw_out) {
        *raw_out = raw;
    }
    for (int zone = 0; zone < 3; ++zone) {
        if (((raw >> (zone * 2)) & 0x03) != 0) {
            return true;
        }
    }
    return false;
}

bool init_display_touch()
{
    if (!g_i2c_bus) {
        return false;
    }
    const esp_err_t probe = i2c_master_probe(g_i2c_bus, 0x38, 200);
    if (probe != ESP_OK) {
        ESP_LOGW(kTag, "FT6336 display touch not found at 0x38: %s", esp_err_to_name(probe));
        g_display_touch_ready = false;
        return false;
    }

    g_display_touch = std::make_unique<I2cDevice>(g_i2c_bus, 0x38, 400 * 1000);
    uint8_t chip_id = 0;
    ESP_ERROR_CHECK_WITHOUT_ABORT(g_display_touch->try_read_reg(0xA3, chip_id));
    g_display_touch_ready = true;
    ESP_LOGI(kTag, "FT6336 display touch ready: chip_id=0x%02x", chip_id);
    return true;
}

bool read_display_touch_pressed(int* out_x = nullptr, int* out_y = nullptr, uint8_t* raw_points_out = nullptr)
{
    if (!g_display_touch_ready || !g_display_touch) {
        return false;
    }

    uint8_t buf[6] = {};
    const esp_err_t err = g_display_touch->try_read(0x02, buf, sizeof(buf));
    if (err != ESP_OK) {
        return false;
    }

    const int points = buf[0] & 0x0F;
    if (raw_points_out) {
        *raw_points_out = static_cast<uint8_t>(points);
    }
    const bool pressed = points > 0;
    if (pressed) {
        const int x = ((buf[1] & 0x0F) << 8) | buf[2];
        const int y = ((buf[3] & 0x0F) << 8) | buf[4];
        if (out_x) {
            *out_x = x;
        }
        if (out_y) {
            *out_y = y;
        }
    }
    return pressed;
}

bool set_i2c_bit(I2cDevice& device, uint8_t reg, uint8_t bit, bool enabled)
{
    uint8_t value = 0;
    esp_err_t err = device.try_read_reg(reg, value);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "read reg 0x%02x failed: %s", reg, esp_err_to_name(err));
        return false;
    }

    if (enabled) {
        value |= (1 << bit);
    } else {
        value &= ~(1 << bit);
    }

    err = device.try_write_reg(reg, value);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "write reg 0x%02x failed: %s", reg, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool set_neon_range(int start_index, int led_count, uint8_t r, uint8_t g, uint8_t b)
{
    if (!g_neon_ready || !g_py32) {
        return false;
    }

    const uint16_t color = rgb565(r, g, b);
    const uint8_t payload[2] = {
        static_cast<uint8_t>(color & 0xFF),
        static_cast<uint8_t>((color >> 8) & 0xFF),
    };

    bool ok = true;
    for (int i = 0; i < led_count; ++i) {
        const int index = start_index + i;
        if (index < 0 || index >= 12) {
            continue;
        }
        ok = (g_py32->try_write(0x30 + index * 2, payload, sizeof(payload)) == ESP_OK) && ok;
    }

    uint8_t led_cfg = 0;
    ok = (g_py32->try_read_reg(0x24, led_cfg) == ESP_OK) && ok;
    ok = (g_py32->try_write_reg(0x24, led_cfg | (1 << 6)) == ESP_OK) && ok;
    return ok;
}

bool set_neon_pixel_raw(int index, uint8_t r, uint8_t g, uint8_t b)
{
    if (!g_neon_ready || !g_py32 || index < 0 || index >= 12) {
        return false;
    }

    const uint16_t color = rgb565(r, g, b);
    const uint8_t payload[2] = {
        static_cast<uint8_t>(color & 0xFF),
        static_cast<uint8_t>((color >> 8) & 0xFF),
    };
    return g_py32->try_write(0x30 + index * 2, payload, sizeof(payload)) == ESP_OK;
}

bool show_neon_pixels()
{
    if (!g_neon_ready || !g_py32) {
        return false;
    }
    uint8_t led_cfg = 0;
    bool ok = g_py32->try_read_reg(0x24, led_cfg) == ESP_OK;
    ok = (g_py32->try_write_reg(0x24, led_cfg | (1 << 6)) == ESP_OK) && ok;
    return ok;
}

void color_wheel(int pos, uint8_t* r, uint8_t* g, uint8_t* b)
{
    pos = ((pos % 256) + 256) % 256;
    if (pos < 85) {
        *r = static_cast<uint8_t>(255 - pos * 3);
        *g = static_cast<uint8_t>(pos * 3);
        *b = 0;
    } else if (pos < 170) {
        pos -= 85;
        *r = 0;
        *g = static_cast<uint8_t>(255 - pos * 3);
        *b = static_cast<uint8_t>(pos * 3);
    } else {
        pos -= 170;
        *r = static_cast<uint8_t>(pos * 3);
        *g = 0;
        *b = static_cast<uint8_t>(255 - pos * 3);
    }
}

bool init_robot_body_power()
{
    if (!g_i2c_bus) {
        return false;
    }
    g_py32 = std::make_unique<I2cDevice>(g_i2c_bus, kPy32Addr, 100 * 1000);
    I2cDevice& py32 = *g_py32;

    uint8_t version = 0;
    bool ready = false;
    for (int attempt = 0; attempt < 6; ++attempt) {
        vTaskDelay(pdMS_TO_TICKS(200));
        const esp_err_t err = py32.try_read_reg(0x02, version);
        if (err == ESP_OK && version != 0x00 && version != 0xFF) {
            ready = true;
            break;
        }
        ESP_LOGI(kTag, "PY32 not ready: attempt=%d err=%s version=0x%02x",
                 attempt + 1, esp_err_to_name(err), version);
    }

    if (!ready) {
        ESP_LOGW(kTag, "PY32 IO expander not found; servos and neon disabled");
        g_py32.reset();
        return false;
    }

    bool ok = true;
    ok = set_i2c_bit(py32, 0x03, 0, true) && ok;
    ok = set_i2c_bit(py32, 0x0B, 0, false) && ok;
    ok = set_i2c_bit(py32, 0x09, 0, true) && ok;
    ok = set_i2c_bit(py32, 0x05, 0, false) && ok;

    ok = set_i2c_bit(py32, 0x04, 5, true) && ok;
    ok = set_i2c_bit(py32, 0x0C, 5, false) && ok;
    ok = set_i2c_bit(py32, 0x0A, 5, true) && ok;
    ok = set_i2c_bit(py32, 0x14, 5, false) && ok;
    ok = (py32.try_write_reg(0x24, 12) == ESP_OK) && ok;

    g_neon_ready = ok;
    ESP_LOGI(kTag, "PY32 ready version=0x%02x neon=%s servo_vm=off",
             version, g_neon_ready ? "ready" : "disabled");
    return ok;
}

bool set_servo_vm_power(bool enabled)
{
    if (!g_py32) {
        return false;
    }
    const bool ok = set_i2c_bit(*g_py32, 0x05, 0, enabled);
    ESP_LOGI(kTag, "servo VM_EN %s: %s", enabled ? "on" : "off", ok ? "ok" : "failed");
    if (enabled) {
        vTaskDelay(pdMS_TO_TICKS(300));
    }
    return ok;
}

void set_backlight_brightness(uint8_t brightness)
{
    if (!g_pmic) {
        return;
    }

    brightness = std::min<uint8_t>(brightness, 100);
    if (brightness == 0) {
        uint8_t val = 0;
        if (g_pmic->try_read_reg(0x90, val) == ESP_OK) {
            ESP_ERROR_CHECK_WITHOUT_ABORT(g_pmic->try_write_reg(0x90, val & 0x7F));
        }
        return;
    }

    const uint8_t reg_val = 20 + (static_cast<uint16_t>(brightness) * 8 / 100);
    ESP_ERROR_CHECK_WITHOUT_ABORT(g_pmic->try_write_reg(0x99, reg_val));

    uint8_t val = 0;
    if (g_pmic->try_read_reg(0x90, val) == ESP_OK && !(val & 0x80)) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(g_pmic->try_write_reg(0x90, val | 0x80));
    }
}

bool read_battery_status(int& level_pct, bool& charging, bool& discharging,
                         bool& charging_done, bool& usb_power_present, int& current_direction)
{
    level_pct = -1;
    charging = false;
    discharging = false;
    charging_done = false;
    usb_power_present = false;
    current_direction = -1;
    if (!g_pmic) {
        return false;
    }

    uint8_t input_status = 0;
    uint8_t power_status = 0;
    uint8_t level = 0;
    if (g_pmic->try_read_reg(0x00, input_status) != ESP_OK ||
        g_pmic->try_read_reg(0x01, power_status) != ESP_OK ||
        g_pmic->try_read_reg(0xA4, level) != ESP_OK) {
        return false;
    }

    current_direction = (power_status & 0b01100000) >> 5;
    charging = current_direction == 1;
    discharging = current_direction == 2;
    charging_done = (power_status & 0b00000111) == 0b00000100;
    usb_power_present = (input_status & 0b00100000) != 0;
    level_pct = clamp_int(static_cast<int>(level), 0, 100);
    return true;
}

void update_battery_status()
{
    int pct = -1;
    bool charging = false;
    bool discharging = false;
    bool charging_done = false;
    bool usb_power_present = false;
    int current_direction = -1;
    const bool known = read_battery_status(pct, charging, discharging,
                                           charging_done, usb_power_present, current_direction);
    g_battery_known = known;
    g_battery_pct = known ? pct : -1;
    g_battery_charging = known && charging;
    g_battery_discharging = known && discharging;
    g_battery_charging_done = known && charging_done;
    g_usb_power_present = known && usb_power_present;
    g_battery_current_direction = known ? current_direction : -1;
}

void init_power_and_reset_panel()
{
    if (!g_i2c_mutex) {
        g_i2c_mutex = xSemaphoreCreateMutex();
    }

    i2c_master_bus_config_t bus_config = {};
    bus_config.i2c_port = I2C_NUM_1;
    bus_config.sda_io_num = kI2cSda;
    bus_config.scl_io_num = kI2cScl;
    bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_config.glitch_ignore_cnt = 7;
    bus_config.flags.enable_internal_pullup = 1;

    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &g_i2c_bus));

    g_pmic = std::make_unique<I2cDevice>(g_i2c_bus, kPmicAddr);
    g_pmic->write_reg(0x90, 0xB4);
    g_pmic->write_reg(0x97, 0b11110 - 2);
    g_pmic->write_reg(0x69, 0b00110101);
    g_pmic->write_reg(0x30, 0b111111);
    g_pmic->write_reg(0x90, 0xBF);
    g_pmic->write_reg(0x94, 33 - 5);
    g_pmic->write_reg(0x95, 33 - 5);
    g_pmic->write_reg(0x27, 0x00);
    set_backlight_brightness(g_display_brightness_pct);

    I2cDevice io(g_i2c_bus, kAw9523Addr);
    io.write_reg(0x02, 0b00000111);
    io.write_reg(0x03, 0b10001111);
    io.write_reg(0x04, 0b00011000);
    io.write_reg(0x05, 0b00001100);
    io.write_reg(0x11, 0b00010000);
    io.write_reg(0x12, 0b11111111);
    io.write_reg(0x13, 0b11111111);
    io.write_reg(0x03, 0b10000001);
    vTaskDelay(pdMS_TO_TICKS(20));
    io.write_reg(0x03, 0b10000011);
    vTaskDelay(pdMS_TO_TICKS(10));

    init_head_touch();
    init_display_touch();
    init_robot_body_power();
}

void init_display()
{
    spi_bus_config_t bus_config = {};
    bus_config.mosi_io_num = GPIO_NUM_37;
    bus_config.miso_io_num = GPIO_NUM_NC;
    bus_config.sclk_io_num = GPIO_NUM_36;
    bus_config.quadwp_io_num = GPIO_NUM_NC;
    bus_config.quadhd_io_num = GPIO_NUM_NC;
    bus_config.max_transfer_sz = kWidth * kHeight * sizeof(uint16_t);
    ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &bus_config, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num = GPIO_NUM_3;
    io_config.dc_gpio_num = GPIO_NUM_35;
    io_config.spi_mode = 2;
    io_config.pclk_hz = 40 * 1000 * 1000;
    io_config.trans_queue_depth = 10;
    io_config.lcd_cmd_bits = 8;
    io_config.lcd_param_bits = 8;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &g_panel_io));

    esp_lcd_panel_dev_config_t panel_config = {};
    panel_config.reset_gpio_num = GPIO_NUM_NC;
    panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    panel_config.bits_per_pixel = 16;
    ESP_ERROR_CHECK(esp_lcd_new_panel_ili9341(g_panel_io, &panel_config, &g_panel));

    ESP_ERROR_CHECK(esp_lcd_panel_reset(g_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(g_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(g_panel, false));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(g_panel, false, false));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));
}

void init_framebuffer()
{
    g_display_mutex = xSemaphoreCreateMutex();
    if (!g_display_mutex) {
        ESP_LOGW(kTag, "display mutex unavailable");
    }
    g_framebuffer = static_cast<uint16_t*>(
        heap_caps_malloc(kFrameBufferBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!g_framebuffer) {
        g_framebuffer = static_cast<uint16_t*>(heap_caps_malloc(kFrameBufferBytes, MALLOC_CAP_8BIT));
    }
    if (g_framebuffer) {
        std::fill_n(g_framebuffer, kWidth * kHeight, kBlack);
        ESP_LOGI(kTag, "display framebuffer ready: %u bytes", static_cast<unsigned>(kFrameBufferBytes));
    } else {
        ESP_LOGW(kTag, "display framebuffer unavailable; using direct drawing");
    }
}

bool begin_frame()
{
    if (!g_framebuffer) {
        return false;
    }
    if (g_display_mutex && xSemaphoreTake(g_display_mutex, pdMS_TO_TICKS(250)) != pdTRUE) {
        ESP_LOGW(kTag, "display framebuffer busy; falling back to direct drawing");
        return false;
    }
    g_framebuffer_active = true;
    return true;
}

void flush_frame()
{
    if (!g_framebuffer_active || !g_framebuffer) {
        return;
    }
    g_framebuffer_active = false;
    if (g_panel) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(
            esp_lcd_panel_draw_bitmap(g_panel, 0, 0, kWidth, kHeight, g_framebuffer));
    }
    if (g_display_mutex) {
        xSemaphoreGive(g_display_mutex);
    }
}

void draw_face_extras();

struct FrameGuard {
    bool active;

    FrameGuard() : active(begin_frame()) {}

    ~FrameGuard()
    {
        if (active) {
            flush_frame();
        }
    }
};

struct FaceFrameGuard {
    bool active;

    FaceFrameGuard() : active(begin_frame()) {}

    ~FaceFrameGuard()
    {
        if (active) {
            draw_face_extras();
            flush_frame();
        }
    }
};

void set_lcd_sleep(bool sleeping)
{
    if (!g_panel || !g_panel_io) {
        return;
    }
    if (sleeping == g_display_sleeping) {
        return;
    }

    if (sleeping) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_io_tx_param(g_panel_io, 0x28, nullptr, 0));
        vTaskDelay(pdMS_TO_TICKS(30));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_io_tx_param(g_panel_io, 0x10, nullptr, 0));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_disp_on_off(g_panel, false));
        set_backlight_brightness(0);
        g_display_sleeping = true;
    } else {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_io_tx_param(g_panel_io, 0x11, nullptr, 0));
        vTaskDelay(pdMS_TO_TICKS(120));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_io_tx_param(g_panel_io, 0x29, nullptr, 0));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_lcd_panel_disp_on_off(g_panel, true));
        set_backlight_brightness(g_display_brightness_pct);
        g_display_sleeping = false;
    }
}

void wake_display_if_needed()
{
    if (g_display_sleeping) {
        set_lcd_sleep(false);
    }
}

void draw_rect(int x, int y, int w, int h, uint16_t color)
{
    if (w <= 0 || h <= 0) {
        return;
    }
    x = std::max(0, x);
    y = std::max(0, y);
    w = std::min(w, kWidth - x);
    h = std::min(h, kHeight - y);
    if (w <= 0 || h <= 0) {
        return;
    }

    if (g_framebuffer_active && g_framebuffer) {
        for (int yy = 0; yy < h; ++yy) {
            std::fill_n(g_framebuffer + (y + yy) * kWidth + x, w, color);
        }
        return;
    }

    if (!g_panel) {
        return;
    }

    static uint16_t row[kWidth];
    std::fill_n(row, w, color);
    for (int yy = 0; yy < h; ++yy) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(
            esp_lcd_panel_draw_bitmap(g_panel, x, y + yy, x + w, y + yy + 1, row));
    }
}

void draw_line(int x0, int y0, int x1, int y1, uint16_t color, int thickness = 4)
{
    const int dx = std::abs(x1 - x0);
    const int sx = x0 < x1 ? 1 : -1;
    const int dy = -std::abs(y1 - y0);
    const int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;

    while (true) {
        draw_rect(x0 - thickness / 2, y0 - thickness / 2, thickness, thickness, color);
        if (x0 == x1 && y0 == y1) {
            break;
        }
        const int e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
}

void draw_ellipse(int cx, int cy, int rx, int ry, uint16_t color)
{
    if (rx <= 0 || ry <= 0) {
        return;
    }
    for (int y = -ry; y <= ry; ++y) {
        const float normalized = 1.0f - (static_cast<float>(y * y) / static_cast<float>(ry * ry));
        const int span = static_cast<int>(std::sqrt(std::max(0.0f, normalized)) * rx);
        draw_rect(cx - span, cy + y, span * 2 + 1, 1, color);
    }
}

void draw_mouth_curve(int cx, int cy, int width, int height, bool smile, uint16_t color)
{
    const int half = std::max(4, width / 2);
    const int direction = smile ? 1 : -1;
    const int shoulder = cy + direction * height * 2 / 3;
    const int center = cy + direction * height;
    draw_line(cx - half, cy, cx - half / 2, shoulder, color, 3);
    draw_line(cx - half / 2, shoulder, cx, center, color, 3);
    draw_line(cx, center, cx + half / 2, shoulder, color, 3);
    draw_line(cx + half / 2, shoulder, cx + half, cy, color, 3);
}

void clear(uint16_t color)
{
    draw_rect(0, 0, kWidth, kHeight, color);
}

void reset_voice_meter()
{
    g_voice_level_pct = 0;
    g_voice_avg_level = 0;
    g_voice_peak_level = 0;
    g_voice_active = false;
    std::fill(g_voice_waveform.begin(), g_voice_waveform.end(), 0);
    g_voice_waveform_head = 0;
}

void push_voice_level(int level_pct, int avg, int peak, bool active)
{
    level_pct = clamp_int(level_pct, 0, 100);
    g_voice_level_pct = level_pct;
    g_voice_avg_level = avg;
    g_voice_peak_level = peak;
    g_voice_active = active;
    const int next = (static_cast<int>(g_voice_waveform_head) + 1) %
                     static_cast<int>(g_voice_waveform.size());
    g_voice_waveform[next] = static_cast<uint8_t>(level_pct);
    g_voice_waveform_head = next;
}

void draw_voice_waveform_overlay()
{
    const int panel_x = 20;
    const int panel_y = 198;
    const int panel_w = 280;
    const int panel_h = 34;
    const int mid_y = panel_y + panel_h / 2;
    const uint16_t background = rgb565(0, 4, 8);
    const uint16_t dim = rgb565(10, 55, 70);
    const uint16_t idle = rgb565(40, 130, 180);
    const uint16_t active = rgb565(80, 255, 130);
    const uint16_t color = g_voice_active ? active : idle;

    draw_rect(panel_x, panel_y, panel_w, panel_h, background);
    draw_line(panel_x + 8, mid_y, panel_x + panel_w - 8, mid_y, dim, 1);

    const int bars = std::min<int>(static_cast<int>(g_voice_waveform.size()), (panel_w - 16) / 4);
    const int latest = static_cast<int>(g_voice_waveform_head);
    for (int i = 0; i < bars; ++i) {
        const int history_index =
            (latest - (bars - 1 - i) + static_cast<int>(g_voice_waveform.size()) * 2) %
            static_cast<int>(g_voice_waveform.size());
        const int level = clamp_int(static_cast<int>(g_voice_waveform[history_index]), 0, 100);
        const int bar_h = std::max(2, level * (panel_h - 8) / 100);
        const int x = panel_x + 8 + i * 4;
        draw_line(x, mid_y - bar_h / 2, x, mid_y + bar_h / 2, color, 2);
    }
}

void draw_face_extras()
{
    if (g_face_extra_mode == FaceExtraMode::VoiceWaveform) {
        draw_voice_waveform_overlay();
    }
}

const uint8_t* glyph_for(char raw)
{
    static const uint8_t space[5] = {0, 0, 0, 0, 0};
    static const uint8_t unknown[5] = {0x02, 0x01, 0x59, 0x09, 0x06};
    static const uint8_t glyphs[][5] = {
        {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
        {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
        {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39},
        {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
        {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E},
        {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36},
        {0x3E, 0x41, 0x41, 0x41, 0x22}, {0x7F, 0x41, 0x41, 0x22, 0x1C},
        {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01},
        {0x3E, 0x41, 0x49, 0x49, 0x7A}, {0x7F, 0x08, 0x08, 0x08, 0x7F},
        {0x00, 0x41, 0x7F, 0x41, 0x00}, {0x20, 0x40, 0x41, 0x3F, 0x01},
        {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40},
        {0x7F, 0x02, 0x0C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F},
        {0x3E, 0x41, 0x41, 0x41, 0x3E}, {0x7F, 0x09, 0x09, 0x09, 0x06},
        {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46},
        {0x46, 0x49, 0x49, 0x49, 0x31}, {0x01, 0x01, 0x7F, 0x01, 0x01},
        {0x3F, 0x40, 0x40, 0x40, 0x3F}, {0x1F, 0x20, 0x40, 0x20, 0x1F},
        {0x3F, 0x40, 0x38, 0x40, 0x3F}, {0x63, 0x14, 0x08, 0x14, 0x63},
        {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43},
    };

    const char c = static_cast<char>(std::toupper(static_cast<unsigned char>(raw)));
    if (c >= '0' && c <= '9') {
        return glyphs[c - '0'];
    }
    if (c >= 'A' && c <= 'Z') {
        return glyphs[10 + c - 'A'];
    }
    if (c == ' ') {
        return space;
    }
    if (c == ':' || c == '.') {
        static const uint8_t dot[5] = {0x00, 0x36, 0x36, 0x00, 0x00};
        return dot;
    }
    if (c == '-' || c == '_') {
        static const uint8_t dash[5] = {0x08, 0x08, 0x08, 0x08, 0x08};
        return dash;
    }
    if (c == '!') {
        static const uint8_t bang[5] = {0x00, 0x00, 0x5F, 0x00, 0x00};
        return bang;
    }
    return unknown;
}

void draw_char(int x, int y, char c, int scale, uint16_t color)
{
    const uint8_t* glyph = glyph_for(c);
    for (int col = 0; col < 5; ++col) {
        for (int row = 0; row < 7; ++row) {
            if (glyph[col] & (1 << row)) {
                draw_rect(x + col * scale, y + row * scale, scale, scale, color);
            }
        }
    }
}

void draw_text(int x, int y, const char* text, int scale, uint16_t color)
{
    int cursor = x;
    for (const char* p = text; p && *p; ++p) {
        draw_char(cursor, y, *p, scale, color);
        cursor += 6 * scale;
    }
}

void draw_centered_text(int y, const char* text, int scale, uint16_t color)
{
    const int len = static_cast<int>(std::strlen(text));
    const int width = std::max(1, len * 6 * scale - scale);
    draw_text(std::max(0, (kWidth - width) / 2), y, text, scale, color);
}

void copy_ui_mode(const char* mode)
{
    const char* value = mode && *mode ? mode : "face";
    std::strncpy(g_ui_mode, value, sizeof(g_ui_mode) - 1);
    g_ui_mode[sizeof(g_ui_mode) - 1] = '\0';
}

void draw_wrapped_message(const char* title, const char* message, uint16_t accent)
{
    wake_display_if_needed();
    copy_ui_mode("display");
    FrameGuard frame;
    clear(kBlack);
    draw_centered_text(18, title, 2, accent);
    draw_rect(26, 48, 268, 2, accent);

    const int text_len = static_cast<int>(std::strlen(message));
    const int scale = text_len <= 28 ? 4 : text_len <= 70 ? 3 : 2;
    const int max_chars = std::max(8, kWidth / (6 * scale) - 2);
    const int line_height = 9 * scale;
    int y = 78;
    int lines = 0;
    const char* cursor = message;

    while (*cursor && lines < 5) {
        while (*cursor == ' ') {
            ++cursor;
        }
        int take = 0;
        int last_space = -1;
        while (cursor[take] && take < max_chars) {
            if (cursor[take] == ' ') {
                last_space = take;
            }
            ++take;
        }
        if (cursor[take] && last_space > 0) {
            take = last_space;
        }
        char line[64] = {};
        const int copy_len = std::min<int>(take, sizeof(line) - 1);
        std::memcpy(line, cursor, copy_len);
        line[copy_len] = '\0';
        draw_centered_text(y, line, scale, rgb565(240, 250, 255));
        y += line_height;
        ++lines;
        cursor += take;
    }
}

int count_words(const char* text)
{
    int count = 0;
    bool in_word = false;
    for (const char* p = text; p && *p; ++p) {
        const bool space = std::isspace(static_cast<unsigned char>(*p)) != 0;
        if (space) {
            in_word = false;
        } else if (!in_word) {
            in_word = true;
            ++count;
        }
    }
    return count;
}

void draw_response_panel(int pulse)
{
    const uint16_t panel = rgb565(2, 12, 20);
    const uint16_t border = rgb565(12, 62, 82);
    const uint16_t glow = rgb565(8, 95, 120);
    const int x = 16;
    const int y = 56;
    const int w = 288;
    const int h = 118;

    draw_rect(x + 2, y + 2, w - 4, h - 4, panel);
    draw_line(x + 10, y, x + w - 10, y, border, 2);
    draw_line(x + 10, y + h, x + w - 10, y + h, border, 2);
    draw_line(x, y + 10, x, y + h - 10, border, 2);
    draw_line(x + w, y + 10, x + w, y + h - 10, border, 2);
    draw_ellipse(x + 10, y + 10, 10, 10, border);
    draw_ellipse(x + w - 10, y + 10, 10, 10, border);
    draw_ellipse(x + 10, y + h - 10, 10, 10, border);
    draw_ellipse(x + w - 10, y + h - 10, 10, 10, border);

    if (pulse > 0) {
        draw_line(x + 32, y + 8, x + 92 + pulse * 12, y + 8, glow, 1);
        draw_line(x + w - 92 - pulse * 12, y + h - 8, x + w - 32, y + h - 8, glow, 1);
    }
}

void draw_response_mini_face(int index, uint16_t color)
{
    const int cx = 160;
    const int y = 204;
    const int look = (index % 5) - 2;
    const int blink = index % 11 == 0 ? 1 : 0;

    if (blink) {
        draw_line(cx - 52, y - 12, cx - 28, y - 12, color, 3);
        draw_line(cx + 28, y - 12, cx + 52, y - 12, color, 3);
    } else {
        draw_ellipse(cx - 42 + look, y - 14, 9, 16, color);
        draw_ellipse(cx + 42 + look, y - 14, 9, 16, color);
    }
    draw_mouth_curve(cx + look, y + 6, 48, 14, true, color);
}

void draw_response_progress(int index, int total, uint16_t accent)
{
    if (total <= 1) {
        return;
    }
    const int dots = std::min(total, 12);
    const int start_x = 160 - (dots * 14) / 2;
    const int active = clamp_int((index * dots + total - 1) / total, 1, dots);
    for (int i = 0; i < dots; ++i) {
        const uint16_t color = i < active ? accent : rgb565(12, 45, 58);
        draw_ellipse(start_x + i * 14, 184, i < active ? 3 : 2, i < active ? 3 : 2, color);
    }
}

void draw_word_message(const char* title, const char* word, int index, int total, uint16_t accent, int phase)
{
    wake_display_if_needed();
    copy_ui_mode("display");
    FrameGuard frame;
    clear(rgb565(0, 3, 7));
    const uint16_t label = rgb565(90, 150, 170);
    const uint16_t text = rgb565(246, 252, 255);
    const uint16_t soft = rgb565(150, 225, 235);

    draw_text(22, 18, title, 1, label);
    draw_text(244, 18, "RX", 1, label);
    draw_response_panel(phase);

    const int len = static_cast<int>(std::strlen(word));
    const int scale = len <= 6 ? 6 : len <= 9 ? 5 : len <= 13 ? 4 : len <= 18 ? 3 : 2;
    const int text_height = 7 * scale;
    draw_centered_text(108 - text_height / 2 + phase, word, scale, text);

    draw_response_progress(index, total, accent);
    draw_response_mini_face(index + phase, soft);
}

void draw_word_sequence(const char* title, const char* text, int duration_ms, uint16_t accent)
{
    const int total = std::max(1, count_words(text));
    const int per_word_ms = clamp_int(duration_ms / total, 360, 1150);
    const char* cursor = text;
    int index = 0;

    while (cursor && *cursor) {
        while (*cursor && std::isspace(static_cast<unsigned char>(*cursor))) {
            ++cursor;
        }
        if (!*cursor) {
            break;
        }
        char word[64] = {};
        int len = 0;
        while (*cursor && !std::isspace(static_cast<unsigned char>(*cursor)) && len < static_cast<int>(sizeof(word) - 1)) {
            word[len++] = *cursor++;
        }
        word[len] = '\0';
        while (*cursor && !std::isspace(static_cast<unsigned char>(*cursor))) {
            ++cursor;
        }
        ++index;
        const int frame_ms = std::max(70, per_word_ms / 3);
        draw_word_message(title, word, index, total, accent, 0);
        vTaskDelay(pdMS_TO_TICKS(frame_ms));
        draw_word_message(title, word, index, total, accent, 1);
        vTaskDelay(pdMS_TO_TICKS(frame_ms));
        draw_word_message(title, word, index, total, accent, 0);
        vTaskDelay(pdMS_TO_TICKS(std::max(70, per_word_ms - frame_ms * 2)));
    }
}

void copy_face_emotion(const char* emotion, int intensity_pct)
{
    const char* value = emotion && *emotion ? emotion : "neutral";
    std::strncpy(g_face_emotion, value, sizeof(g_face_emotion) - 1);
    g_face_emotion[sizeof(g_face_emotion) - 1] = '\0';
    g_face_intensity_pct = clamp_int(intensity_pct, 0, 100);
}

bool is_transient_face_emotion(const char* emotion)
{
    return std::strcmp(emotion, "blink") == 0 ||
           std::strcmp(emotion, "glance_left") == 0 ||
           std::strcmp(emotion, "glance_right") == 0 ||
           std::strcmp(emotion, "glance_up") == 0 ||
           std::strcmp(emotion, "glance_down") == 0 ||
           std::strcmp(emotion, "mouth_smile") == 0 ||
           std::strcmp(emotion, "mouth_tiny") == 0 ||
           std::strcmp(emotion, "mouth_wiggle") == 0 ||
           std::strcmp(emotion, "look_left") == 0 ||
           std::strcmp(emotion, "look_right") == 0 ||
           std::strcmp(emotion, "look_up") == 0 ||
           std::strcmp(emotion, "look_down") == 0 ||
           std::strcmp(emotion, "breathe") == 0 ||
           std::strcmp(emotion, "deep_breathe") == 0 ||
           std::strcmp(emotion, "micro_sleep") == 0 ||
           std::strcmp(emotion, "wink_left") == 0 ||
           std::strcmp(emotion, "wink_right") == 0 ||
           std::strcmp(emotion, "surprise_pop") == 0 ||
           std::strcmp(emotion, "grumble") == 0 ||
           std::strcmp(emotion, "yawn") == 0 ||
           std::strcmp(emotion, "happy_squint") == 0;
}

void draw_face(const char* emotion, int intensity_pct);

void draw_open_eyes(int left_x, int right_x, int eye_y, int rx, int ry,
                    int pupil_dx, int pupil_dy, uint16_t eye_color)
{
    draw_ellipse(left_x, eye_y, rx, ry, eye_color);
    draw_ellipse(right_x, eye_y, rx, ry, eye_color);
    const int pupil_rx = clamp_int(rx / 2, 5, 10);
    const int pupil_ry = clamp_int(ry / 3, 7, 13);
    const int max_dx = clamp_int(rx - pupil_rx - 2, 0, 18);
    const int max_dy = clamp_int(ry - pupil_ry - 2, 0, 14);
    pupil_dx = clamp_int(pupil_dx, -max_dx, max_dx);
    pupil_dy = clamp_int(pupil_dy, -max_dy, max_dy);
    draw_ellipse(left_x + pupil_dx, eye_y + pupil_dy, pupil_rx, pupil_ry, kBlack);
    draw_ellipse(right_x + pupil_dx, eye_y + pupil_dy, pupil_rx, pupil_ry, kBlack);
}

void draw_life_face_frame(const char* base_emotion, int intensity_pct,
                          int eye_dx, int eye_dy, int eye_ry,
                          int pupil_dx = 0, int pupil_dy = 0,
                          int mouth_mode = 0)
{
    wake_display_if_needed();
    copy_ui_mode(g_face_extra_mode == FaceExtraMode::VoiceWaveform ? "recording" : "face");
    FaceFrameGuard frame;

    const uint16_t white = rgb565(245, 250, 255);
    const uint16_t warm = rgb565(255, 230, 120);
    const uint16_t face_color = std::strcmp(base_emotion, "happy") == 0 ||
                                        std::strcmp(base_emotion, "love") == 0
                                    ? warm
                                    : white;
    const int pulse = clamp_int(intensity_pct / 18, 0, 6);
    const int left_x = 105 + eye_dx;
    const int right_x = 215 + eye_dx;
    const int eye_y = 92 + eye_dy;
    const int mouth_y = 154 + eye_dy / 4;

    clear(kBlack);

    if (eye_ry <= 4) {
        draw_line(left_x - 24, eye_y, left_x + 24, eye_y, face_color, 5);
        draw_line(right_x - 24, eye_y, right_x + 24, eye_y, face_color, 5);
    } else {
        draw_open_eyes(left_x, right_x, eye_y, 13 + pulse, eye_ry,
                       pupil_dx, pupil_dy, face_color);
    }

    if (mouth_mode == 1) {
        draw_mouth_curve(160 + eye_dx / 4, mouth_y - 10, 82, 32 + pulse, true, white);
    } else if (mouth_mode == 2) {
        draw_mouth_curve(160 + eye_dx / 4, mouth_y, 42, 10, true, white);
    } else if (mouth_mode == 3) {
        draw_line(126, mouth_y - 2, 146, mouth_y + 7, white, 3);
        draw_line(146, mouth_y + 7, 166, mouth_y - 2, white, 3);
        draw_line(166, mouth_y - 2, 188, mouth_y + 7, white, 3);
    } else if (std::strcmp(base_emotion, "sad") == 0) {
        draw_mouth_curve(160 + eye_dx / 4, mouth_y + 22, 72, 24, false, white);
    } else if (std::strcmp(base_emotion, "happy") == 0 || std::strcmp(base_emotion, "love") == 0) {
        draw_mouth_curve(160 + eye_dx / 4, mouth_y - 8, 76, 28 + pulse, true, white);
    } else {
        draw_mouth_curve(160 + eye_dx / 4, mouth_y - 4, 62, 18, true, white);
    }
}

void animate_transient_face(const char* emotion, int intensity_pct)
{
    char base_emotion[24] = {};
    std::strncpy(base_emotion, g_face_emotion, sizeof(base_emotion) - 1);
    if (is_transient_face_emotion(base_emotion)) {
        std::strncpy(base_emotion, "neutral", sizeof(base_emotion) - 1);
    }
    const int base_intensity = clamp_int(g_face_intensity_pct > 0 ? g_face_intensity_pct : intensity_pct, 0, 100);

    if (std::strcmp(emotion, "blink") == 0) {
        const int eye_heights[] = {28, 18, 8, 3, 8, 18, 28};
        for (int eye_height : eye_heights) {
            draw_life_face_frame(base_emotion, base_intensity, 0, 0, eye_height);
            vTaskDelay(pdMS_TO_TICKS(42));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "breathe") == 0) {
        const int offsets[] = {0, 1, 2, 3, 2, 1, 0, -1, -2, -1, 0};
        for (int offset : offsets) {
            draw_life_face_frame(base_emotion,
                                 clamp_int(base_intensity + offset, 0, 100),
                                 0,
                                 0,
                                 28 + clamp_int(offset, -2, 3));
            vTaskDelay(pdMS_TO_TICKS(90));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "deep_breathe") == 0) {
        const int offsets[] = {0, 1, 2, 4, 5, 6, 5, 4, 2, 1, 0, -1, -2, -1, 0};
        for (int offset : offsets) {
            draw_life_face_frame(base_emotion,
                                 clamp_int(base_intensity + offset, 0, 100),
                                 0,
                                 clamp_int(offset / 2, -1, 3),
                                 28 + clamp_int(offset, -2, 6),
                                 0,
                                 0,
                                 offset > 3 ? 1 : 0);
            vTaskDelay(pdMS_TO_TICKS(135));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "micro_sleep") == 0) {
        const int eye_heights[] = {20, 10, 3, 3, 3, 10, 20};
        for (int i = 0; i < 7; ++i) {
            draw_life_face_frame(base_emotion,
                                 base_intensity,
                                 0,
                                 2,
                                 eye_heights[i],
                                 0,
                                 0,
                                 2);
            if (i >= 2 && i <= 4) {
                draw_centered_text(44, "Z", 2, rgb565(150, 210, 255));
                draw_centered_text(26, "Z", 1, rgb565(90, 150, 220));
            }
            vTaskDelay(pdMS_TO_TICKS(i >= 2 && i <= 4 ? 180 : 90));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "wink_left") == 0 ||
        std::strcmp(emotion, "wink_right") == 0) {
        const bool left_closed = std::strcmp(emotion, "wink_left") == 0;
        const int pupil_shift = left_closed ? 4 : -4;
        const int phases[] = {0, 1, 2, 2, 1, 0};
        for (int phase : phases) {
            FaceFrameGuard frame;
            clear(kBlack);
            const uint16_t white = rgb565(245, 250, 255);
            const int pulse = clamp_int(base_intensity / 18, 0, 6);
            const int left_x = 105;
            const int right_x = 215;
            const int eye_y = 92;
            const int mouth_y = 154;
            if (left_closed) {
                draw_line(left_x - 24, eye_y, left_x + 24, eye_y + phase, white, 5);
                draw_open_eyes(right_x, right_x, eye_y, 13 + pulse, 28 + pulse, pupil_shift, 0, white);
            } else {
                draw_open_eyes(left_x, left_x, eye_y, 13 + pulse, 28 + pulse, pupil_shift, 0, white);
                draw_line(right_x - 24, eye_y + phase, right_x + 24, eye_y, white, 5);
            }
            draw_mouth_curve(160 + pupil_shift, mouth_y - 8, 70, 24 + pulse, true, white);
            vTaskDelay(pdMS_TO_TICKS(80));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "surprise_pop") == 0) {
        const int sizes[] = {0, 4, 10, 4, 0};
        for (int size : sizes) {
            draw_life_face_frame(base_emotion,
                                 clamp_int(base_intensity + size, 0, 100),
                                 0,
                                 -size / 3,
                                 30 + size,
                                 0,
                                 -size / 4);
            draw_ellipse(160, 154, 18 + size, 18 + size / 2, rgb565(245, 250, 255));
            draw_ellipse(160, 154, 8 + size / 2, 8 + size / 4, kBlack);
            vTaskDelay(pdMS_TO_TICKS(95));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "happy_squint") == 0) {
        const int offsets[] = {0, 2, 4, 4, 2, 0};
        for (int offset : offsets) {
            FaceFrameGuard frame;
            clear(kBlack);
            const uint16_t warm = rgb565(255, 230, 120);
            const uint16_t white = rgb565(245, 250, 255);
            draw_mouth_curve(105, 86 + offset, 46, 18, false, warm);
            draw_mouth_curve(215, 86 + offset, 46, 18, false, warm);
            draw_mouth_curve(160, 146, 88, 34 + offset, true, white);
            vTaskDelay(pdMS_TO_TICKS(105));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "grumble") == 0) {
        const int wiggles[] = {0, 3, -2, 2, 0};
        for (int wiggle : wiggles) {
            draw_life_face_frame(base_emotion,
                                 clamp_int(base_intensity - 6, 0, 100),
                                 0,
                                 2,
                                 26,
                                 wiggle / 2,
                                 0,
                                 3);
            draw_line(126, 170 + wiggle, 194, 166 - wiggle, rgb565(245, 250, 255), 3);
            vTaskDelay(pdMS_TO_TICKS(110));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "yawn") == 0) {
        const int sizes[] = {2, 8, 16, 22, 18, 8, 2};
        for (int i = 0; i < 7; ++i) {
            const int size = sizes[i];
            draw_life_face_frame(base_emotion,
                                 clamp_int(base_intensity - 8, 0, 100),
                                 0,
                                 3,
                                 clamp_int(24 - size / 2, 4, 28),
                                 0,
                                 1,
                                 0);
            draw_ellipse(160, 158, 24 + size, 10 + size, rgb565(245, 250, 255));
            draw_ellipse(160, 158, 12 + size / 2, 4 + size / 2, kBlack);
            vTaskDelay(pdMS_TO_TICKS(i >= 2 && i <= 4 ? 160 : 95));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    if (std::strcmp(emotion, "mouth_smile") == 0 ||
        std::strcmp(emotion, "mouth_tiny") == 0 ||
        std::strcmp(emotion, "mouth_wiggle") == 0) {
        const int mouth_modes[] = {
            0,
            std::strcmp(emotion, "mouth_smile") == 0 ? 1 : std::strcmp(emotion, "mouth_tiny") == 0 ? 2 : 3,
            std::strcmp(emotion, "mouth_smile") == 0 ? 1 : std::strcmp(emotion, "mouth_tiny") == 0 ? 2 : 3,
            0,
        };
        const int eye_offsets[] = {0, 1, 1, 0};
        for (int i = 0; i < 4; ++i) {
            draw_life_face_frame(base_emotion,
                                 base_intensity,
                                 0,
                                 0,
                                 28 + clamp_int(eye_offsets[i], 0, 2),
                                 0,
                                 0,
                                 mouth_modes[i]);
            vTaskDelay(pdMS_TO_TICKS(110));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    int target_pupil_dx = 0;
    int target_pupil_dy = 0;
    if (std::strcmp(emotion, "glance_left") == 0) {
        target_pupil_dx = -9;
    } else if (std::strcmp(emotion, "glance_right") == 0) {
        target_pupil_dx = 9;
    } else if (std::strcmp(emotion, "glance_up") == 0) {
        target_pupil_dy = -9;
    } else if (std::strcmp(emotion, "glance_down") == 0) {
        target_pupil_dy = 9;
    }

    if (target_pupil_dx != 0 || target_pupil_dy != 0) {
        const float phases[] = {0.0f, 0.45f, 0.85f, 1.0f, 1.0f, 0.75f, 0.30f, 0.0f};
        for (float phase : phases) {
            draw_life_face_frame(base_emotion,
                                 base_intensity,
                                 0,
                                 0,
                                 28 + clamp_int(base_intensity / 24, 0, 4),
                                 static_cast<int>(std::lround(target_pupil_dx * phase)),
                                 static_cast<int>(std::lround(target_pupil_dy * phase)));
            vTaskDelay(pdMS_TO_TICKS(95));
        }
        draw_face(base_emotion, base_intensity);
        return;
    }

    int target_dx = 0;
    int target_dy = 0;
    if (std::strcmp(emotion, "look_left") == 0) {
        target_dx = -18;
    } else if (std::strcmp(emotion, "look_right") == 0) {
        target_dx = 18;
    } else if (std::strcmp(emotion, "look_up") == 0) {
        target_dy = -10;
    } else if (std::strcmp(emotion, "look_down") == 0) {
        target_dy = 12;
    }

    const float phases[] = {0.0f, 0.35f, 0.70f, 1.0f, 1.0f, 0.70f, 0.35f, 0.0f};
    for (float phase : phases) {
        draw_life_face_frame(base_emotion,
                             base_intensity,
                             static_cast<int>(std::lround(target_dx * phase)),
                             static_cast<int>(std::lround(target_dy * phase)),
                             28 + clamp_int(base_intensity / 24, 0, 4));
        vTaskDelay(pdMS_TO_TICKS(80));
    }
    draw_face(base_emotion, base_intensity);
}

void draw_face(const char* emotion, int intensity_pct)
{
    wake_display_if_needed();
    copy_ui_mode(g_face_extra_mode == FaceExtraMode::VoiceWaveform ? "recording" : "face");
    copy_face_emotion(emotion, intensity_pct);
    FaceFrameGuard frame;

    const uint16_t white = rgb565(245, 250, 255);
    const uint16_t cyan = rgb565(20, 180, 255);
    const uint16_t warm = rgb565(255, 230, 120);
    const uint16_t green = rgb565(80, 255, 130);
    const uint16_t red = rgb565(255, 55, 70);
    const uint16_t face_color = std::strcmp(g_face_emotion, "angry") == 0 ||
                                        std::strcmp(g_face_emotion, "error") == 0 ||
                                        std::strcmp(g_face_emotion, "battery_low") == 0
                                    ? red
                                : std::strcmp(g_face_emotion, "happy") == 0 ||
                                        std::strcmp(g_face_emotion, "love") == 0
                                    ? warm
                                : std::strcmp(g_face_emotion, "charging") == 0 ||
                                        std::strcmp(g_face_emotion, "battery") == 0
                                    ? green
                                    : white;
    const uint16_t accent = std::strcmp(g_face_emotion, "sleep") == 0 ? rgb565(80, 130, 160) : cyan;
    const int pulse = clamp_int(intensity_pct / 18, 0, 6);
    const int left_x = 105;
    const int right_x = 215;
    const int eye_y = 92;
    const int mouth_y = 154;

    clear(kBlack);

    if (std::strcmp(g_face_emotion, "blink") == 0) {
        draw_line(left_x - 26, eye_y, left_x + 26, eye_y, face_color, 5);
        draw_line(right_x - 26, eye_y, right_x + 26, eye_y, face_color, 5);
        draw_mouth_curve(160, mouth_y - 4, 62, 18, true, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "look_left") == 0 ||
        std::strcmp(g_face_emotion, "look_right") == 0 ||
        std::strcmp(g_face_emotion, "look_up") == 0 ||
        std::strcmp(g_face_emotion, "look_down") == 0) {
        const int dx = std::strcmp(g_face_emotion, "look_left") == 0 ? -18 :
                       std::strcmp(g_face_emotion, "look_right") == 0 ? 18 : 0;
        const int dy = std::strcmp(g_face_emotion, "look_up") == 0 ? -10 :
                       std::strcmp(g_face_emotion, "look_down") == 0 ? 12 : 0;
        draw_open_eyes(left_x + dx, right_x + dx, eye_y + dy,
                       13 + pulse, 28 + pulse, 0, 0, face_color);
        draw_mouth_curve(160 + dx / 3, mouth_y - 4 + dy / 3, 62, 18, true, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "sleep") == 0) {
        draw_line(left_x - 24, eye_y, left_x + 24, eye_y, accent, 5);
        draw_line(right_x - 24, eye_y, right_x + 24, eye_y, accent, 5);
        draw_line(150, mouth_y, 170, mouth_y + 6, white, 3);
        draw_line(170, mouth_y + 6, 190, mouth_y, white, 3);
        return;
    }

    if (std::strcmp(g_face_emotion, "happy") == 0) {
        draw_mouth_curve(left_x, eye_y - 10, 44 + pulse, 18, false, face_color);
        draw_mouth_curve(right_x, eye_y - 10, 44 + pulse, 18, false, face_color);
        draw_mouth_curve(160, mouth_y - 8, 76, 28 + pulse, true, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "angry") == 0) {
        draw_line(left_x - 26, eye_y - 22, left_x + 20, eye_y + 16, face_color, 6);
        draw_line(right_x + 26, eye_y - 22, right_x - 20, eye_y + 16, face_color, 6);
        draw_line(125, mouth_y + 8, 195, mouth_y + 2, face_color, 5);
        return;
    }

    if (std::strcmp(g_face_emotion, "sad") == 0) {
        draw_open_eyes(left_x, right_x, eye_y, 13, 28 + pulse, 0, 2, face_color);
        draw_mouth_curve(160, mouth_y + 22, 72, 24, false, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "surprised") == 0 || std::strcmp(g_face_emotion, "question") == 0) {
        draw_open_eyes(left_x, right_x, eye_y, 22 + pulse, 30 + pulse, 0, 0, face_color);
        if (std::strcmp(g_face_emotion, "question") == 0) {
            draw_centered_text(mouth_y - 20, "?", 5, white);
        } else {
            draw_ellipse(160, mouth_y, 20 + pulse, 24 + pulse, white);
        }
        return;
    }

    if (std::strcmp(g_face_emotion, "wink") == 0) {
        draw_line(left_x - 22, eye_y, left_x + 22, eye_y, face_color, 5);
        draw_open_eyes(right_x, right_x, eye_y, 13 + pulse, 28 + pulse, 0, 0, face_color);
        draw_mouth_curve(160, mouth_y - 8, 62, 22, true, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "speaking") == 0) {
        draw_open_eyes(left_x, right_x, eye_y, 14 + pulse, 29 + pulse, 0, 0, face_color);
        draw_ellipse(160, mouth_y, 38 + pulse * 2, 18 + pulse, accent);
        draw_ellipse(160, mouth_y, 24 + pulse, 10 + pulse / 2, kBlack);
        return;
    }

    if (std::strcmp(g_face_emotion, "error") == 0) {
        draw_line(left_x - 20, eye_y - 20, left_x + 20, eye_y + 20, red, 5);
        draw_line(left_x + 20, eye_y - 20, left_x - 20, eye_y + 20, red, 5);
        draw_line(right_x - 20, eye_y - 20, right_x + 20, eye_y + 20, red, 5);
        draw_line(right_x + 20, eye_y - 20, right_x - 20, eye_y + 20, red, 5);
        draw_mouth_curve(160, mouth_y + 20, 74, 24, false, red);
        return;
    }

    if (std::strcmp(g_face_emotion, "battery") == 0 ||
        std::strcmp(g_face_emotion, "charging") == 0 ||
        std::strcmp(g_face_emotion, "battery_low") == 0) {
        const uint16_t battery_color = std::strcmp(g_face_emotion, "battery_low") == 0
                                           ? red
                                       : std::strcmp(g_face_emotion, "charging") == 0
                                           ? green
                                           : warm;
        draw_open_eyes(left_x, right_x, eye_y, 13 + pulse, 28 + pulse, 0, 0, face_color);
        const int x0 = 112;
        const int x1 = 204;
        const int y0 = mouth_y - 16;
        const int y1 = mouth_y + 16;
        draw_line(x0, y0, x1, y0, battery_color, 4);
        draw_line(x0, y1, x1, y1, battery_color, 4);
        draw_line(x0, y0, x0, y1, battery_color, 4);
        draw_line(x1, y0, x1, y1, battery_color, 4);
        draw_line(x1 + 7, mouth_y - 8, x1 + 7, mouth_y + 8, battery_color, 6);
        if (std::strcmp(g_face_emotion, "charging") == 0) {
            draw_line(166, mouth_y - 13, 154, mouth_y + 3, white, 4);
            draw_line(154, mouth_y + 3, 172, mouth_y + 3, white, 4);
            draw_line(172, mouth_y + 3, 158, mouth_y + 15, white, 4);
        } else if (std::strcmp(g_face_emotion, "battery_low") == 0) {
            draw_line(130, mouth_y + 20, 190, mouth_y + 18, red, 4);
        } else {
            for (int x = 124; x <= 184; x += 10) {
                draw_line(x, mouth_y - 8, x, mouth_y + 8, green, 6);
            }
        }
        return;
    }

    draw_open_eyes(left_x, right_x, eye_y, 13 + pulse, 28 + pulse, 0, 0, face_color);
    draw_mouth_curve(160, mouth_y - 4, 62, 18, true, white);
}

void display_boot()
{
    draw_wrapped_message("HERMES2STACKCHAN", "MQTT DISPLAY READY", rgb565(0, 220, 230));
}

void display_error(const char* message)
{
    draw_wrapped_message("ERROR", message, rgb565(255, 50, 50));
}

void save_device_settings()
{
    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open("ui_state", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "device settings save failed: %s", esp_err_to_name(err));
        return;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "volume", g_speaker_volume_pct));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "bright", static_cast<int>(g_display_brightness_pct)));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "led_mode", static_cast<int>(g_led_mode)));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "led_r", static_cast<int>(g_led_r)));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "led_g", static_cast<int>(g_led_g)));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(handle, "led_b", static_cast<int>(g_led_b)));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_commit(handle));
    nvs_close(handle);
}

void load_device_settings()
{
    nvs_handle_t handle = 0;
    const esp_err_t open_err = nvs_open("ui_state", NVS_READONLY, &handle);
    if (open_err != ESP_OK) {
        return;
    }

    int32_t value = 0;
    if (nvs_get_i32(handle, "volume", &value) == ESP_OK) {
        g_speaker_volume_pct = clamp_int(static_cast<int>(value), 0, 100);
    }
    if (nvs_get_i32(handle, "bright", &value) == ESP_OK) {
        g_display_brightness_pct = static_cast<uint8_t>(clamp_int(static_cast<int>(value), 0, 100));
    }
    if (nvs_get_i32(handle, "led_mode", &value) == ESP_OK) {
        g_led_mode = clamp_int(static_cast<int>(value), 0, 6);
    }
    if (nvs_get_i32(handle, "led_r", &value) == ESP_OK) {
        g_led_r = clamp_int(static_cast<int>(value), 0, 255);
    }
    if (nvs_get_i32(handle, "led_g", &value) == ESP_OK) {
        g_led_g = clamp_int(static_cast<int>(value), 0, 255);
    }
    if (nvs_get_i32(handle, "led_b", &value) == ESP_OK) {
        g_led_b = clamp_int(static_cast<int>(value), 0, 255);
    }
    nvs_close(handle);
}

void set_speaker_volume_pct(int volume)
{
    volume = clamp_int(volume, 0, 100);
    g_speaker_volume_pct = volume;
    if (g_audio_output_ready && g_audio_output) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_set_out_vol(g_audio_output, volume));
    }
    save_device_settings();
    ESP_LOGI(kTag, "speaker volume set to %d%%", volume);
}

void set_display_brightness_pct(int brightness)
{
    brightness = clamp_int(brightness, 0, 100);
    g_display_brightness_pct = static_cast<uint8_t>(brightness);
    if (!g_display_sleeping) {
        set_backlight_brightness(g_display_brightness_pct);
    }
    save_device_settings();
    ESP_LOGI(kTag, "display brightness set to %d%%", brightness);
}

bool init_speaker()
{
    if (!g_i2c_bus) {
        return false;
    }

    i2s_chan_config_t chan_cfg = {
        .id = I2S_NUM_0,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = 6,
        .dma_frame_num = 240,
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
        .intr_priority = 0,
    };
    esp_err_t err = i2s_new_channel(&chan_cfg, &g_audio_tx, &g_audio_rx);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "audio i2s channels failed: %s", esp_err_to_name(err));
        return false;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg = {
            .sample_rate_hz = kAudioSampleRate,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol = false,
            .bit_shift = true,
            .left_align = true,
            .big_endian = false,
            .bit_order_lsb = false,
        },
        .gpio_cfg = {
            .mclk = kAudioMclk,
            .bclk = kAudioBclk,
            .ws = kAudioWs,
            .dout = kAudioDout,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    i2s_tdm_config_t tdm_cfg = {
        .clk_cfg = {
            .sample_rate_hz = kAudioSampleRate,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
            .bclk_div = 8,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = i2s_tdm_slot_mask_t(I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3),
            .ws_width = I2S_TDM_AUTO_WS_WIDTH,
            .ws_pol = false,
            .bit_shift = true,
            .left_align = false,
            .big_endian = false,
            .bit_order_lsb = false,
            .skip_mask = false,
            .total_slot = I2S_TDM_AUTO_SLOT_NUM,
        },
        .gpio_cfg = {
            .mclk = kAudioMclk,
            .bclk = kAudioBclk,
            .ws = kAudioWs,
            .dout = I2S_GPIO_UNUSED,
            .din = kAudioDin,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    err = i2s_channel_init_std_mode(g_audio_tx, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "speaker i2s std init failed: %s", esp_err_to_name(err));
        return false;
    }
    err = i2s_channel_init_tdm_mode(g_audio_rx, &tdm_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "microphone i2s tdm init failed: %s", esp_err_to_name(err));
        return false;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_enable(g_audio_tx));
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_enable(g_audio_rx));

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_0,
        .rx_handle = g_audio_rx,
        .tx_handle = g_audio_tx,
    };
    g_audio_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    if (!g_audio_data_if) {
        ESP_LOGW(kTag, "speaker i2s data iface failed");
        return false;
    }

    audio_codec_i2c_cfg_t out_i2c_cfg = {
        .port = I2C_NUM_1,
        .addr = kAw88298Addr,
        .bus_handle = g_i2c_bus,
    };
    g_audio_out_ctrl_if = audio_codec_new_i2c_ctrl(&out_i2c_cfg);
    g_audio_gpio_if = audio_codec_new_gpio();
    if (!g_audio_out_ctrl_if || !g_audio_gpio_if) {
        ESP_LOGW(kTag, "speaker output control iface failed");
        return false;
    }

    aw88298_codec_cfg_t aw88298_cfg = {};
    aw88298_cfg.ctrl_if = g_audio_out_ctrl_if;
    aw88298_cfg.gpio_if = g_audio_gpio_if;
    aw88298_cfg.reset_pin = GPIO_NUM_NC;
    aw88298_cfg.hw_gain.pa_voltage = 5.0;
    aw88298_cfg.hw_gain.codec_dac_voltage = 3.3;
    aw88298_cfg.hw_gain.pa_gain = 1;
    g_audio_out_codec_if = aw88298_codec_new(&aw88298_cfg);
    if (!g_audio_out_codec_if) {
        ESP_LOGW(kTag, "speaker AW88298 codec failed");
        return false;
    }

    esp_codec_dev_cfg_t out_dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = g_audio_out_codec_if,
        .data_if = g_audio_data_if,
    };
    g_audio_output = esp_codec_dev_new(&out_dev_cfg);
    if (!g_audio_output) {
        ESP_LOGW(kTag, "speaker codec dev failed");
        return false;
    }

    esp_codec_dev_sample_info_t out_info = {
        .bits_per_sample = 16,
        .channel = 1,
        .channel_mask = 0,
        .sample_rate = kAudioSampleRate,
        .mclk_multiple = 0,
    };
    err = static_cast<esp_err_t>(esp_codec_dev_open(g_audio_output, &out_info));
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "speaker open failed: %s", esp_err_to_name(err));
        return false;
    }

    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_set_out_vol(g_audio_output, g_speaker_volume_pct));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_set_out_mute(g_audio_output, false));
    g_audio_output_ready = true;
    ESP_LOGI(kTag, "speaker ready: AW88298 addr=0x%02x volume=%d", kAw88298Addr, g_speaker_volume_pct);
    return true;
}

bool init_microphone()
{
    if (!g_i2c_bus || !g_audio_data_if || !g_audio_rx) {
        ESP_LOGW(kTag, "microphone skipped: audio bus not ready");
        g_audio_input_ready = false;
        return false;
    }

    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = I2C_NUM_1,
        .addr = kEs7210Addr,
        .bus_handle = g_i2c_bus,
    };
    g_audio_in_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    if (!g_audio_in_ctrl_if) {
        ESP_LOGW(kTag, "microphone i2c ctrl iface failed");
        g_audio_input_ready = false;
        return false;
    }

    es7210_codec_cfg_t es7210_cfg = {};
    es7210_cfg.ctrl_if = g_audio_in_ctrl_if;
    es7210_cfg.mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3;
    g_audio_in_codec_if = es7210_codec_new(&es7210_cfg);
    if (!g_audio_in_codec_if) {
        ESP_LOGW(kTag, "microphone ES7210 codec failed");
        g_audio_input_ready = false;
        return false;
    }

    esp_codec_dev_cfg_t dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = g_audio_in_codec_if,
        .data_if = g_audio_data_if,
    };
    g_audio_input = esp_codec_dev_new(&dev_cfg);
    if (!g_audio_input) {
        ESP_LOGW(kTag, "microphone codec dev failed");
        g_audio_input_ready = false;
        return false;
    }

    esp_codec_dev_sample_info_t sample_info = {
        .bits_per_sample = 16,
        .channel = 2,
        .channel_mask = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0),
        .sample_rate = kAudioSampleRate,
        .mclk_multiple = 0,
    };
    esp_err_t err = static_cast<esp_err_t>(esp_codec_dev_open(g_audio_input, &sample_info));
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "microphone open failed: %s", esp_err_to_name(err));
        g_audio_input_ready = false;
        return false;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(
        esp_codec_dev_set_in_channel_gain(g_audio_input, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0), 30.0f));

    g_audio_input_ready = true;
    reset_voice_meter();
    ESP_LOGI(kTag,
             "microphone ready: ES7210 addr=0x%02x sample_rate=%d din=%d",
             kEs7210Addr,
             kAudioSampleRate,
             static_cast<int>(kAudioDin));
    return true;
}

void play_tone(int frequency_hz, int duration_ms)
{
    if (!g_audio_output_ready || !g_audio_output) {
        ESP_LOGW(kTag, "sound skipped: speaker not ready");
        return;
    }

    frequency_hz = clamp_int(frequency_hz, 120, 4000);
    duration_ms = clamp_int(duration_ms, 20, 2000);
    static constexpr int kChunkSamples = 240;
    static constexpr int kAmplitude = 4800;
    std::array<int16_t, kChunkSamples> tone = {};
    std::array<int16_t, kChunkSamples> silence = {};

    int sample_index = 0;
    const int total_samples = kAudioSampleRate * duration_ms / 1000;
    const int half_period = std::max(1, kAudioSampleRate / (2 * frequency_hz));
    while (sample_index < total_samples) {
        const int count = std::min(kChunkSamples, total_samples - sample_index);
        for (int i = 0; i < count; ++i) {
            tone[i] = (((sample_index + i) / half_period) % 2 == 0) ? kAmplitude : -kAmplitude;
        }
        std::fill(tone.begin() + count, tone.end(), 0);
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(g_audio_output, tone.data(), tone.size() * sizeof(int16_t)));
        sample_index += count;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(g_audio_output, silence.data(), silence.size() * sizeof(int16_t)));
}

void queue_led_command(int mode, int r, int g, int b)
{
    g_led_mode = clamp_int(mode, 0, 6);
    if (r >= 0) {
        g_led_r = clamp_int(r, 0, 255);
    }
    if (g >= 0) {
        g_led_g = clamp_int(g, 0, 255);
    }
    if (b >= 0) {
        g_led_b = clamp_int(b, 0, 255);
    }

    if (g_led_mode == 0) {
        set_neon_range(0, 12, 0, 0, 0);
    } else if (g_led_mode == 1) {
        set_neon_range(0, 12,
                       static_cast<uint8_t>(g_led_r),
                       static_cast<uint8_t>(g_led_g),
                       static_cast<uint8_t>(g_led_b));
    }
    save_device_settings();
    ESP_LOGI(kTag, "led command mode=%d rgb=%d,%d,%d", g_led_mode, g_led_r, g_led_g, g_led_b);
}

const char* led_mode_name(int mode)
{
    switch (mode) {
    case 1:
        return "solid";
    case 2:
        return "rainbow";
    case 3:
        return "scanner";
    case 4:
        return "blink";
    case 5:
        return "breathe";
    case 6:
        return "sparkle";
    default:
        return "off";
    }
}

int led_mode_from_string(const char* mode)
{
    if (!mode || !*mode || std::strcmp(mode, "off") == 0) {
        return 0;
    }
    if (std::strcmp(mode, "solid") == 0 || std::strcmp(mode, "color") == 0) {
        return 1;
    }
    if (std::strcmp(mode, "rainbow") == 0) {
        return 2;
    }
    if (std::strcmp(mode, "scanner") == 0 || std::strcmp(mode, "knight") == 0) {
        return 3;
    }
    if (std::strcmp(mode, "blink") == 0) {
        return 4;
    }
    if (std::strcmp(mode, "breathe") == 0 || std::strcmp(mode, "breath") == 0) {
        return 5;
    }
    if (std::strcmp(mode, "sparkle") == 0 || std::strcmp(mode, "party") == 0) {
        return 6;
    }
    return -1;
}

struct ServoAxis {
    uint8_t id;
    int default_zero;
    int angle_min_tenth_deg;
    int angle_max_tenth_deg;
    int raw_min;
    int raw_max;
};

int axis_zero_position(const ServoAxis& axis)
{
    return axis.default_zero;
}

int angle_to_raw_position(const ServoAxis& axis, int angle_tenth_deg)
{
    return axis_zero_position(axis) + angle_tenth_deg * 16 / 5 / 10;
}

int effective_raw_min(const ServoAxis& axis)
{
    const int angle_min_raw = angle_to_raw_position(axis, axis.angle_min_tenth_deg);
    const int angle_max_raw = angle_to_raw_position(axis, axis.angle_max_tenth_deg);
    return std::max(axis.raw_min, std::min(angle_min_raw, angle_max_raw));
}

int effective_raw_max(const ServoAxis& axis)
{
    const int angle_min_raw = angle_to_raw_position(axis, axis.angle_min_tenth_deg);
    const int angle_max_raw = angle_to_raw_position(axis, axis.angle_max_tenth_deg);
    return std::min(axis.raw_max, std::max(angle_min_raw, angle_max_raw));
}

int target_pct_to_raw_position(const ServoAxis& axis, int target_pct)
{
    target_pct = clamp_int(target_pct, -100, 100);
    const int zero = axis_zero_position(axis);
    if (target_pct == 0) {
        return clamp_int(zero, effective_raw_min(axis), effective_raw_max(axis));
    }
    if (target_pct < 0) {
        const int span = zero - effective_raw_min(axis);
        return clamp_int(zero + span * target_pct / 100, effective_raw_min(axis), effective_raw_max(axis));
    }
    const int span = effective_raw_max(axis) - zero;
    return clamp_int(zero + span * target_pct / 100, effective_raw_min(axis), effective_raw_max(axis));
}

int raw_position_to_target_pct(const ServoAxis& axis, int raw)
{
    const int zero = axis_zero_position(axis);
    raw = clamp_int(raw, effective_raw_min(axis), effective_raw_max(axis));
    if (raw == zero) {
        return 0;
    }
    if (raw < zero) {
        const int span = std::max(1, zero - effective_raw_min(axis));
        return clamp_int((raw - zero) * 100 / span, -100, 0);
    }
    const int span = std::max(1, effective_raw_max(axis) - zero);
    return clamp_int((raw - zero) * 100 / span, 0, 100);
}

void update_servo_temperatures(const ServoAxis& yaw, const ServoAxis& pitch)
{
    g_temperature_servo_yaw_c = sanitize_temperature_c(g_servo_bus.ReadTemper(yaw.id));
    g_temperature_servo_pitch_c = sanitize_temperature_c(g_servo_bus.ReadTemper(pitch.id));
}

void update_servo_state_pct(const ServoAxis& yaw, const ServoAxis& pitch, int yaw_pos, int pitch_pos)
{
    g_servo_yaw_pct = raw_position_to_target_pct(yaw, yaw_pos);
    g_servo_pitch_pct = raw_position_to_target_pct(pitch, pitch_pos);
}

bool wait_for_servo_bus(const ServoAxis& yaw, const ServoAxis& pitch)
{
    for (int attempt = 1; attempt <= 6; ++attempt) {
        const int yaw_ping = g_servo_bus.Ping(yaw.id);
        const int pitch_ping = g_servo_bus.Ping(pitch.id);
        ESP_LOGI(kTag, "servo ping attempt %d: yaw=%d pitch=%d", attempt, yaw_ping, pitch_ping);
        if (yaw_ping == yaw.id && pitch_ping == pitch.id) {
            g_servo_ready = true;
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(220));
    }
    g_servo_ready = false;
    return false;
}

bool read_safe_servo_position_or_keep_last(const ServoAxis& axis, int& position)
{
    const int fresh_position = g_servo_bus.ReadPos(axis.id);
    ESP_LOGI(kTag, "servo %d ReadPos=%d", axis.id, fresh_position);
    if (fresh_position >= effective_raw_min(axis) && fresh_position <= effective_raw_max(axis)) {
        position = fresh_position;
        return true;
    }
    if (position >= effective_raw_min(axis) && position <= effective_raw_max(axis)) {
        ESP_LOGW(kTag, "servo %d feedback invalid; keeping last safe pos=%d", axis.id, position);
        return true;
    }
    return false;
}

void write_safe_servo_position(const ServoAxis& axis, int position)
{
    position = clamp_int(position, effective_raw_min(axis), effective_raw_max(axis));
    g_servo_bus.WritePos(axis.id, position, 20, 0);
}

void write_safe_servo_positions(const ServoAxis& yaw, const ServoAxis& pitch,
                                int yaw_position, int pitch_position)
{
    yaw_position = clamp_int(yaw_position, effective_raw_min(yaw), effective_raw_max(yaw));
    pitch_position = clamp_int(pitch_position, effective_raw_min(pitch), effective_raw_max(pitch));
    u8 ids[2] = {yaw.id, pitch.id};
    u16 positions[2] = {
        static_cast<u16>(yaw_position),
        static_cast<u16>(pitch_position),
    };
    u16 times[2] = {20, 20};
    u16 speeds[2] = {0, 0};
    g_servo_bus.SyncWritePos(ids, 2, positions, times, speeds);
}

void move_axes_toward(const ServoAxis& yaw, const ServoAxis& pitch,
                      int& yaw_current, int& pitch_current,
                      int yaw_target, int pitch_target)
{
    yaw_target = clamp_int(yaw_target, effective_raw_min(yaw), effective_raw_max(yaw));
    pitch_target = clamp_int(pitch_target, effective_raw_min(pitch), effective_raw_max(pitch));

    while (yaw_current != yaw_target || pitch_current != pitch_target) {
        if (yaw_current != yaw_target) {
            const int direction = yaw_target > yaw_current ? 1 : -1;
            yaw_current += direction * std::min(18, std::abs(yaw_target - yaw_current));
            write_safe_servo_position(yaw, yaw_current);
        }
        if (pitch_current != pitch_target) {
            const int direction = pitch_target > pitch_current ? 1 : -1;
            pitch_current += direction * std::min(18, std::abs(pitch_target - pitch_current));
            write_safe_servo_position(pitch, pitch_current);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

float catmull_rom_value(float p0, float p1, float p2, float p3, float t)
{
    const float t2 = t * t;
    const float t3 = t2 * t;
    return 0.5f * ((2.0f * p1) +
                   (-p0 + p2) * t +
                   (2.0f * p0 - 5.0f * p1 + 4.0f * p2 - p3) * t2 +
                   (-p0 + 3.0f * p1 - 3.0f * p2 + p3) * t3);
}

int motion_segment_duration_ms(const ServoAxis& yaw, const ServoAxis& pitch,
                               int yaw_start_raw, int pitch_start_raw,
                               const MotionPoint& target)
{
    if (target.duration_ms > 0) {
        return target.duration_ms;
    }

    const int yaw_start_pct = raw_position_to_target_pct(yaw, yaw_start_raw);
    const int pitch_start_pct = raw_position_to_target_pct(pitch, pitch_start_raw);
    const int pct_distance = std::max(std::abs(target.yaw_pct - yaw_start_pct),
                                      std::abs(target.pitch_pct - pitch_start_pct));
    const int speed_pct = clamp_int(target.speed_pct, 1, 100);
    const float ms_per_pct = 5.0f + (100 - speed_pct) * 0.30f;
    return clamp_int(static_cast<int>(std::lround(std::max(1, pct_distance) * ms_per_pct)), 40, 4000);
}

int safe_motion_steps(int yaw_start_raw, int pitch_start_raw,
                      int yaw_target_raw, int pitch_target_raw,
                      int duration_ms)
{
    const int raw_distance = std::max(std::abs(yaw_target_raw - yaw_start_raw),
                                      std::abs(pitch_target_raw - pitch_start_raw));
    const int min_safe_steps = std::max(1, (raw_distance + 17) / 18);
    const int requested_steps = std::max(1, duration_ms / 20);
    return std::max(min_safe_steps, requested_steps);
}

void write_motion_sample(const ServoAxis& yaw, const ServoAxis& pitch,
                         int& yaw_pos, int& pitch_pos,
                         int yaw_raw, int pitch_raw)
{
    yaw_pos = clamp_int(yaw_raw, effective_raw_min(yaw), effective_raw_max(yaw));
    pitch_pos = clamp_int(pitch_raw, effective_raw_min(pitch), effective_raw_max(pitch));
    write_safe_servo_positions(yaw, pitch, yaw_pos, pitch_pos);
    update_servo_state_pct(yaw, pitch, yaw_pos, pitch_pos);
}

void move_axes_path_segment(const ServoAxis& yaw, const ServoAxis& pitch,
                            int& yaw_pos, int& pitch_pos,
                            int yaw0, int pitch0,
                            int yaw1, int pitch1,
                            int yaw2, int pitch2,
                            int yaw3, int pitch3,
                            int duration_ms,
                            bool spline)
{
    const int steps = safe_motion_steps(yaw1, pitch1, yaw2, pitch2, duration_ms);
    for (int step = 1; step <= steps; ++step) {
        const float t = static_cast<float>(step) / static_cast<float>(steps);
        int yaw_next = 0;
        int pitch_next = 0;
        if (spline) {
            yaw_next = static_cast<int>(std::lround(catmull_rom_value(yaw0, yaw1, yaw2, yaw3, t)));
            pitch_next = static_cast<int>(std::lround(catmull_rom_value(pitch0, pitch1, pitch2, pitch3, t)));
        } else {
            yaw_next = yaw1 + static_cast<int>(std::lround((yaw2 - yaw1) * t));
            pitch_next = pitch1 + static_cast<int>(std::lround((pitch2 - pitch1) * t));
        }
        write_motion_sample(yaw, pitch, yaw_pos, pitch_pos, yaw_next, pitch_next);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void execute_motion_command(const ServoAxis& yaw, const ServoAxis& pitch,
                            int& yaw_pos, int& pitch_pos,
                            const MotionCommand& command)
{
    if (command.point_count <= 0) {
        return;
    }

    int yaw_raw[48] = {};
    int pitch_raw[48] = {};
    for (int i = 0; i < command.point_count; ++i) {
        yaw_raw[i] = target_pct_to_raw_position(yaw, command.points[i].yaw_pct);
        pitch_raw[i] = target_pct_to_raw_position(pitch, command.points[i].pitch_pct);
    }

    const bool spline = command.curve == 1;
    const int first_duration = motion_segment_duration_ms(yaw, pitch, yaw_pos, pitch_pos, command.points[0]);
    move_axes_path_segment(yaw, pitch,
                           yaw_pos, pitch_pos,
                           yaw_pos, pitch_pos,
                           yaw_pos, pitch_pos,
                           yaw_raw[0], pitch_raw[0],
                           command.point_count > 1 ? yaw_raw[1] : yaw_raw[0],
                           command.point_count > 1 ? pitch_raw[1] : pitch_raw[0],
                           first_duration,
                           false);

    if (command.points[0].hold_ms > 0) {
        vTaskDelay(pdMS_TO_TICKS(command.points[0].hold_ms));
    }

    for (int i = 0; i < command.point_count - 1; ++i) {
        const int prev = i > 0 ? i - 1 : i;
        const int next = i + 1;
        const int next2 = i + 2 < command.point_count ? i + 2 : next;
        const int duration_ms = motion_segment_duration_ms(yaw, pitch, yaw_raw[i], pitch_raw[i], command.points[next]);
        move_axes_path_segment(yaw, pitch,
                               yaw_pos, pitch_pos,
                               yaw_raw[prev], pitch_raw[prev],
                               yaw_raw[i], pitch_raw[i],
                               yaw_raw[next], pitch_raw[next],
                               yaw_raw[next2], pitch_raw[next2],
                               duration_ms,
                               spline);
        if (command.points[next].hold_ms > 0) {
            vTaskDelay(pdMS_TO_TICKS(command.points[next].hold_ms));
        }
    }
}

void build_topics()
{
    const char* pair_id = CONFIG_STACKCHAN_PAIR_ID;
    std::snprintf(g_topic_display, sizeof(g_topic_display), "hermes-stackchan/%s/cmd/display", pair_id);
    std::snprintf(g_topic_system, sizeof(g_topic_system), "hermes-stackchan/%s/cmd/system", pair_id);
    std::snprintf(g_topic_face, sizeof(g_topic_face), "hermes-stackchan/%s/cmd/face", pair_id);
    std::snprintf(g_topic_move, sizeof(g_topic_move), "hermes-stackchan/%s/cmd/move", pair_id);
    std::snprintf(g_topic_motion, sizeof(g_topic_motion), "hermes-stackchan/%s/cmd/motion", pair_id);
    std::snprintf(g_topic_sound, sizeof(g_topic_sound), "hermes-stackchan/%s/cmd/sound", pair_id);
    std::snprintf(g_topic_audio, sizeof(g_topic_audio), "hermes-stackchan/%s/cmd/audio", pair_id);
    std::snprintf(g_topic_led, sizeof(g_topic_led), "hermes-stackchan/%s/cmd/led", pair_id);
    std::snprintf(g_topic_device, sizeof(g_topic_device), "hermes-stackchan/%s/cmd/device", pair_id);
    std::snprintf(g_topic_say, sizeof(g_topic_say), "hermes-stackchan/%s/cmd/say", pair_id);
    std::snprintf(g_topic_status, sizeof(g_topic_status), "hermes-stackchan/%s/status", pair_id);
    std::snprintf(g_topic_ack, sizeof(g_topic_ack), "hermes-stackchan/%s/ack", pair_id);
    std::snprintf(g_topic_error, sizeof(g_topic_error), "hermes-stackchan/%s/error", pair_id);
    std::snprintf(g_topic_events, sizeof(g_topic_events), "hermes-stackchan/%s/events", pair_id);
}

bool topic_matches(const char* topic, int topic_len, const char* expected)
{
    return topic_len == static_cast<int>(std::strlen(expected)) &&
           std::strncmp(topic, expected, topic_len) == 0;
}

const char* json_string(cJSON* root, const char* key, const char* fallback = "")
{
    cJSON* item = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(item) && item->valuestring ? item->valuestring : fallback;
}

int json_int(cJSON* root, const char* key, int fallback)
{
    cJSON* item = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsNumber(item) ? item->valueint : fallback;
}

bool json_bool(cJSON* root, const char* key, bool fallback)
{
    cJSON* item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (cJSON_IsBool(item)) {
        return cJSON_IsTrue(item);
    }
    if (cJSON_IsNumber(item)) {
        return item->valueint != 0;
    }
    return fallback;
}

void publish_json(const char* topic, const char* payload, int qos = 1, int retain = 0)
{
    if (!g_mqtt_client || !g_mqtt_connected) {
        return;
    }
    esp_mqtt_client_publish(g_mqtt_client, topic, payload, 0, qos, retain);
}

void publish_ack(const char* request_id, const char* command, const char* message)
{
    char payload[384] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"request_id\":\"%s\",\"command\":\"%s\",\"ok\":true,\"message\":\"%s\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  request_id && *request_id ? request_id : "",
                  command,
                  message);
    publish_json(g_topic_ack, payload);
}

void publish_error(const char* request_id, const char* command, const char* message)
{
    char payload[384] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"request_id\":\"%s\",\"command\":\"%s\",\"ok\":false,\"error\":\"%s\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  request_id && *request_id ? request_id : "",
                  command,
                  message);
    publish_json(g_topic_error, payload);
}

void publish_event(const char* event, const char* source, const char* request_id, const char* message)
{
    char payload[512] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"event\":\"%s\",\"source\":\"%s\",\"request_id\":\"%s\","
                  "\"uptime_ms\":%lld,\"recording\":%s,\"message\":\"%s\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  event,
                  source && *source ? source : "",
                  request_id && *request_id ? request_id : "",
                  static_cast<long long>(esp_timer_get_time() / 1000),
                  g_recording ? "true" : "false",
                  message && *message ? message : "");
    publish_json(g_topic_events, payload);
}

void publish_touch_event(const char* event, const char* source, uint8_t raw, bool pressed, int x, int y)
{
    char payload[640] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"event\":\"%s\",\"source\":\"%s\",\"uptime_ms\":%lld,"
                  "\"touch\":{\"ready\":%s,\"head_ready\":%s,\"display_ready\":%s,"
                  "\"pressed\":%s,\"raw\":%u,\"x\":%d,\"y\":%d},"
                  "\"message\":\"%s\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  event,
                  source && *source ? source : "touch",
                  static_cast<long long>(esp_timer_get_time() / 1000),
                  (g_head_touch_ready || g_display_touch_ready) ? "true" : "false",
                  g_head_touch_ready ? "true" : "false",
                  g_display_touch_ready ? "true" : "false",
                  pressed ? "true" : "false",
                  static_cast<unsigned>(raw),
                  x,
                  y,
                  pressed ? "head touch pressed" : "head touch released");
    ESP_LOGI(kTag, "touch event=%s source=%s raw=0x%02x pressed=%s x=%d y=%d",
             event,
             source && *source ? source : "touch",
             raw,
             pressed ? "true" : "false",
             x,
             y);
    publish_json(g_topic_events, payload);
}

void publish_status()
{
    int rssi = 0;
    wifi_ap_record_t ap = {};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        rssi = ap.rssi;
    }

    update_soc_temperature();
    update_battery_status();

    char payload[2300] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"uptime_ms\":%lld,\"wifi_rssi\":%d,"
                  "\"battery_pct\":%d,\"charging\":%s,\"battery_charging\":%s,"
                  "\"battery_discharging\":%s,\"battery_charging_done\":%s,"
                  "\"battery_known\":%s,\"usb_power\":%s,\"external_power\":%s,"
                  "\"battery_current_direction\":%d,"
                  "\"volume_pct\":%d,\"brightness_pct\":%u,\"display_sleeping\":%s,"
                  "\"temperature\":{\"soc_c\":%d,\"servo_yaw_c\":%d,\"servo_pitch_c\":%d},"
                  "\"wakeword_enabled\":%s,\"recording\":%s,\"speaking\":false,"
                  "\"audio\":{\"input_ready\":%s,\"wakeword_enabled\":%s,\"wakeword\":\"%s\","
                  "\"recording\":%s,\"recording_source\":\"%s\",\"recording_started_ms\":%lld,"
                  "\"recording_min_ms\":%d,\"recording_silence_timeout_ms\":%d,\"recording_max_ms\":%d,"
                  "\"voice_active\":%s,\"voice_level_pct\":%d,\"voice_avg_level\":%d,\"voice_peak_level\":%d},"
                  "\"touch\":{\"ready\":%s,\"head_ready\":%s,\"display_ready\":%s,"
                  "\"pressed\":%s,\"raw\":%d,\"x\":%d,\"y\":%d},"
                  "\"head\":{\"pan_pct\":%d,\"tilt_pct\":%d,\"ready\":%s},"
                  "\"led\":{\"mode\":\"%s\",\"mode_id\":%d,\"r\":%d,\"g\":%d,\"b\":%d,\"ready\":%s},"
                  "\"face\":{\"emotion\":\"%s\",\"intensity_pct\":%d},"
                  "\"ui\":{\"mode\":\"%s\"},"
                  "\"speaker\":{\"ready\":%s,\"volume_pct\":%d},"
                  "\"camera_available\":false,"
                  "\"firmware\":\"1.0.0-mqtt-hardware\","
                  "\"firmware_version\":\"1.0.0-mqtt-hardware\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  static_cast<long long>(esp_timer_get_time() / 1000),
                  rssi,
                  static_cast<int>(g_battery_pct),
                  g_battery_charging ? "true" : "false",
                  g_battery_charging ? "true" : "false",
                  g_battery_discharging ? "true" : "false",
                  g_battery_charging_done ? "true" : "false",
                  g_battery_known ? "true" : "false",
                  g_usb_power_present ? "true" : "false",
                  g_usb_power_present ? "true" : "false",
                  static_cast<int>(g_battery_current_direction),
                  g_speaker_volume_pct,
                  static_cast<unsigned>(g_display_brightness_pct),
                  g_display_sleeping ? "true" : "false",
                  static_cast<int>(g_temperature_soc_c),
                  static_cast<int>(g_temperature_servo_yaw_c),
                  static_cast<int>(g_temperature_servo_pitch_c),
                  g_wakeword_enabled ? "true" : "false",
                  g_recording ? "true" : "false",
                  g_audio_input_ready ? "true" : "false",
                  g_wakeword_enabled ? "true" : "false",
                  g_wakeword,
                  g_recording ? "true" : "false",
                  g_recording_source,
                  static_cast<long long>(g_recording_started_ms),
                  static_cast<int>(g_recording_min_ms),
                  static_cast<int>(g_recording_silence_timeout_ms),
                  static_cast<int>(g_recording_max_ms),
                  g_voice_active ? "true" : "false",
                  static_cast<int>(g_voice_level_pct),
                  static_cast<int>(g_voice_avg_level),
                  static_cast<int>(g_voice_peak_level),
                  (g_head_touch_ready || g_display_touch_ready) ? "true" : "false",
                  g_head_touch_ready ? "true" : "false",
                  g_display_touch_ready ? "true" : "false",
                  g_touch_pressed ? "true" : "false",
                  static_cast<int>(g_touch_raw),
                  static_cast<int>(g_touch_x),
                  static_cast<int>(g_touch_y),
                  static_cast<int>(g_servo_yaw_pct),
                  static_cast<int>(g_servo_pitch_pct),
                  g_servo_ready ? "true" : "false",
                  led_mode_name(static_cast<int>(g_led_mode)),
                  static_cast<int>(g_led_mode),
                  static_cast<int>(g_led_r),
                  static_cast<int>(g_led_g),
                  static_cast<int>(g_led_b),
                  g_neon_ready ? "true" : "false",
                  g_face_emotion,
                  g_face_intensity_pct,
                  g_ui_mode,
                  g_audio_output_ready ? "true" : "false",
                  g_speaker_volume_pct);
    publish_json(g_topic_status, payload, 1, 1);
}

bool append_motion_point(MotionCommand& command, int yaw_pct, int pitch_pct,
                         int duration_ms, int speed_pct, int hold_ms)
{
    if (command.point_count >= static_cast<int>(sizeof(command.points) / sizeof(command.points[0]))) {
        return false;
    }
    MotionPoint& point = command.points[command.point_count++];
    point.yaw_pct = clamp_int(yaw_pct, kYawTargetMinPct, kYawTargetMaxPct);
    point.pitch_pct = clamp_int(pitch_pct, kPitchTargetMinPct, kPitchTargetMaxPct);
    point.duration_ms = duration_ms > 0 ? clamp_int(duration_ms, 40, 4000) : 0;
    point.speed_pct = clamp_int(speed_pct, 1, 100);
    point.hold_ms = clamp_int(hold_ms, 0, 4000);
    return true;
}

bool build_path_motion(cJSON* root, MotionCommand& command)
{
    cJSON* points = cJSON_GetObjectItemCaseSensitive(root, "points");
    if (!cJSON_IsArray(points)) {
        return false;
    }

    const int default_segment_ms = clamp_int(json_int(root, "segment_ms", 0), 0, 4000);
    const int default_speed_pct = clamp_int(json_int(root, "speed_pct", json_int(root, "default_speed_pct", 45)), 1, 100);
    const char* curve = json_string(root, "curve", "linear");
    command.curve = (std::strcmp(curve, "spline") == 0 ||
                     std::strcmp(curve, "smooth") == 0 ||
                     std::strcmp(curve, "catmull_rom") == 0 ||
                     std::strcmp(curve, "catmull-rom") == 0)
                        ? 1
                        : 0;
    command.point_count = 0;
    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, points)
    {
        int yaw = 0;
        int pitch = 0;
        int duration_ms = default_segment_ms;
        int speed_pct = default_speed_pct;
        int hold_ms = 0;
        if (cJSON_IsArray(item)) {
            cJSON* yaw_item = cJSON_GetArrayItem(item, 0);
            cJSON* pitch_item = cJSON_GetArrayItem(item, 1);
            cJSON* duration_item = cJSON_GetArrayItem(item, 2);
            cJSON* speed_item = cJSON_GetArrayItem(item, 3);
            cJSON* hold_item = cJSON_GetArrayItem(item, 4);
            if (!cJSON_IsNumber(yaw_item) || !cJSON_IsNumber(pitch_item)) {
                return false;
            }
            yaw = yaw_item->valueint;
            pitch = pitch_item->valueint;
            if (cJSON_IsNumber(duration_item)) {
                duration_ms = duration_item->valueint;
            }
            if (cJSON_IsNumber(speed_item)) {
                speed_pct = speed_item->valueint;
            }
            if (cJSON_IsNumber(hold_item)) {
                hold_ms = hold_item->valueint;
            }
        } else if (cJSON_IsObject(item)) {
            yaw = json_int(item, "yaw_pct", json_int(item, "yaw", 0));
            pitch = json_int(item, "pitch_pct", json_int(item, "pitch", 0));
            duration_ms = json_int(item, "duration_ms", default_segment_ms);
            speed_pct = json_int(item, "speed_pct", default_speed_pct);
            hold_ms = json_int(item, "hold_ms", 0);
        } else {
            return false;
        }
        if (!append_motion_point(command, yaw, pitch, duration_ms, speed_pct, hold_ms)) {
            return false;
        }
    }
    return command.point_count > 0;
}

bool enqueue_motion_command(const MotionCommand& command)
{
    return g_motion_queue && xQueueSend(g_motion_queue, &command, pdMS_TO_TICKS(50)) == pdTRUE;
}

void copy_cstr(char* destination, size_t destination_len, const char* source)
{
    if (!destination || destination_len == 0) {
        return;
    }
    source = source ? source : "";
    std::strncpy(destination, source, destination_len - 1);
    destination[destination_len - 1] = '\0';
}

void append_ascii_text(char* destination, size_t destination_len, size_t& offset, const char* text)
{
    for (const char* p = text; p && *p && offset + 1 < destination_len; ++p) {
        destination[offset++] = *p;
    }
    destination[offset] = '\0';
}

void copy_display_text(char* destination, size_t destination_len, const char* source)
{
    if (!destination || destination_len == 0) {
        return;
    }
    destination[0] = '\0';
    size_t offset = 0;
    const auto* p = reinterpret_cast<const uint8_t*>(source ? source : "");
    while (*p && offset + 1 < destination_len) {
        if (p[0] == 0xC3 && p[1] != 0) {
            switch (p[1]) {
            case 0x84:
            case 0xA4:
                append_ascii_text(destination, destination_len, offset, "ae");
                p += 2;
                continue;
            case 0x96:
            case 0xB6:
                append_ascii_text(destination, destination_len, offset, "oe");
                p += 2;
                continue;
            case 0x9C:
            case 0xBC:
                append_ascii_text(destination, destination_len, offset, "ue");
                p += 2;
                continue;
            case 0x9F:
                append_ascii_text(destination, destination_len, offset, "ss");
                p += 2;
                continue;
            default:
                break;
            }
        }
        const char c = static_cast<char>(*p++);
        destination[offset++] = (static_cast<unsigned char>(c) < 128) ? c : ' ';
        destination[offset] = '\0';
    }
}

bool enqueue_ui_command(const UiCommand& command)
{
    return g_ui_queue && xQueueSend(g_ui_queue, &command, pdMS_TO_TICKS(50)) == pdTRUE;
}

bool extract_json_string(const char* json, const char* key, char* out, size_t out_len)
{
    if (!json || !key || !out || out_len == 0) {
        return false;
    }
    out[0] = '\0';
    char pattern[64] = {};
    std::snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char* p = std::strstr(json, pattern);
    if (!p) {
        return false;
    }
    p = std::strchr(p + std::strlen(pattern), ':');
    if (!p) {
        return false;
    }
    ++p;
    while (*p == ' ' || *p == '\t') {
        ++p;
    }
    if (*p != '"') {
        return false;
    }
    ++p;
    size_t n = 0;
    while (*p && *p != '"' && n + 1 < out_len) {
        if (*p == '\\' && p[1]) {
            ++p;
        }
        out[n++] = *p++;
    }
    out[n] = '\0';
    return n > 0;
}

void enqueue_direction_glance(int yaw_change_pct, int pitch_change_pct)
{
    const int deadband = 3;
    const char* emotion = "";
    if (std::abs(yaw_change_pct) >= std::abs(pitch_change_pct) &&
        std::abs(yaw_change_pct) > deadband) {
        emotion = yaw_change_pct < 0 ? "glance_left" : "glance_right";
    } else if (std::abs(pitch_change_pct) > deadband) {
        emotion = pitch_change_pct < 0 ? "glance_down" : "glance_up";
    }
    if (!*emotion) {
        return;
    }

    UiCommand glance = {};
    glance.type = UiCommandType::Face;
    glance.intensity_pct = clamp_int(g_face_intensity_pct > 0 ? g_face_intensity_pct : 60, 35, 90);
    copy_cstr(glance.emotion, sizeof(glance.emotion), emotion);
    enqueue_ui_command(glance);
}

void handle_display_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "display", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    const char* mode = json_string(root, "mode");
    const char* text = json_string(root, "text");
    if (std::strcmp(mode, "text") != 0 || !text || !*text) {
        publish_error(request_id, "display", "expected mode text and non-empty text");
        cJSON_Delete(root);
        return;
    }

    UiCommand command = {};
    command.type = UiCommandType::Display;
    command.accent = rgb565(0, 220, 230);
    command.duration_ms = clamp_int(json_int(root, "duration_ms", 3500), 500, 10000);
    copy_display_text(command.text, sizeof(command.text), text);
    copy_cstr(command.emotion, sizeof(command.emotion), "neutral");
    command.intensity_pct = g_face_intensity_pct;
    if (!enqueue_ui_command(command)) {
        publish_error(request_id, "display", "ui queue full");
        cJSON_Delete(root);
        return;
    }

    publish_ack(request_id, "display", "display queued");
    cJSON_Delete(root);
}

void handle_face_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "face", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    const char* emotion = json_string(root, "emotion", "neutral");
    int intensity_pct = json_int(root, "intensity_pct", -1);
    if (intensity_pct < 0) {
        cJSON* intensity = cJSON_GetObjectItemCaseSensitive(root, "intensity");
        if (cJSON_IsNumber(intensity)) {
            intensity_pct = intensity->valuedouble <= 1.0 ? static_cast<int>(intensity->valuedouble * 100.0)
                                                          : intensity->valueint;
        } else {
            intensity_pct = 60;
        }
    }
    intensity_pct = clamp_int(intensity_pct, 0, 100);

    UiCommand command = {};
    command.type = UiCommandType::Face;
    copy_cstr(command.emotion, sizeof(command.emotion), emotion);
    command.intensity_pct = intensity_pct;
    if (!enqueue_ui_command(command)) {
        publish_error(request_id, "face", "ui queue full");
        cJSON_Delete(root);
        return;
    }

    publish_ack(request_id, "face", "face queued");
    cJSON_Delete(root);
}

void handle_move_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "move", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    const char* direction = json_string(root, "direction");
    int yaw_delta = json_int(root, "yaw_delta", json_int(root, "pan_delta", 0));
    int pitch_delta = json_int(root, "pitch_delta", json_int(root, "tilt_delta", 0));
    int yaw_target_pct = json_int(root, "yaw_target_pct", json_int(root, "pan_pct", 101));
    int pitch_target_pct = json_int(root, "pitch_target_pct", json_int(root, "tilt_pct", 101));

    if (direction && *direction) {
        if (std::strcmp(direction, "left") == 0) {
            yaw_target_pct = -100;
        } else if (std::strcmp(direction, "right") == 0) {
            yaw_target_pct = 100;
        } else if (std::strcmp(direction, "center") == 0 || std::strcmp(direction, "straight") == 0) {
            yaw_target_pct = kDefaultIdleYawPct;
            pitch_target_pct = kDefaultIdlePitchPct;
        } else if (std::strcmp(direction, "up") == 0) {
            pitch_target_pct = kPitchTargetMaxPct;
        } else if (std::strcmp(direction, "down") == 0) {
            pitch_target_pct = kPitchTargetMinPct;
        }
    }

    const bool has_yaw_target = yaw_target_pct >= kYawTargetMinPct && yaw_target_pct <= kYawTargetMaxPct;
    const bool has_pitch_target = pitch_target_pct >= kPitchTargetMinPct && pitch_target_pct <= kPitchTargetMaxPct;
    yaw_delta = clamp_int(yaw_delta, -80, 80);
    pitch_delta = clamp_int(pitch_delta, -80, 80);

    if (yaw_delta == 0 && pitch_delta == 0 && !has_yaw_target && !has_pitch_target) {
        publish_error(request_id, "move", "no movement requested");
        cJSON_Delete(root);
        return;
    }

    const int glance_yaw = has_yaw_target ? clamp_int(yaw_target_pct, kYawTargetMinPct, kYawTargetMaxPct) - static_cast<int>(g_servo_yaw_pct)
                                          : yaw_delta;
    const int glance_pitch = has_pitch_target ? clamp_int(pitch_target_pct, kPitchTargetMinPct, kPitchTargetMaxPct) - static_cast<int>(g_servo_pitch_pct)
                                              : pitch_delta;
    enqueue_direction_glance(glance_yaw, glance_pitch);

    g_pending_yaw_delta += yaw_delta;
    g_pending_pitch_delta += pitch_delta;
    if (has_yaw_target) {
        g_pending_yaw_target_pct = clamp_int(yaw_target_pct, kYawTargetMinPct, kYawTargetMaxPct);
    }
    if (has_pitch_target) {
        g_pending_pitch_target_pct = clamp_int(pitch_target_pct, kPitchTargetMinPct, kPitchTargetMaxPct);
    }
    publish_ack(request_id, "move", "movement queued");
    cJSON_Delete(root);
}

void handle_motion_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "motion", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    MotionCommand command = {};
    const bool ok = build_path_motion(root, command);
    if (!ok || command.point_count <= 0) {
        publish_error(request_id, "motion", "points array required");
        cJSON_Delete(root);
        return;
    }
    int glance_yaw = 0;
    int glance_pitch = 0;
    for (int i = 0; i < command.point_count; ++i) {
        glance_yaw = command.points[i].yaw_pct - static_cast<int>(g_servo_yaw_pct);
        glance_pitch = command.points[i].pitch_pct - static_cast<int>(g_servo_pitch_pct);
        if (std::abs(glance_yaw) > 3 || std::abs(glance_pitch) > 3) {
            break;
        }
    }
    enqueue_direction_glance(glance_yaw, glance_pitch);

    if (!enqueue_motion_command(command)) {
        publish_error(request_id, "motion", "motion queue full");
        cJSON_Delete(root);
        return;
    }

    publish_ack(request_id, "motion", "motion queued");
    cJSON_Delete(root);
}

void handle_sound_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "sound", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    SoundCommand command = {
        .frequency_hz = json_int(root, "frequency_hz", json_int(root, "hz", 880)),
        .duration_ms = json_int(root, "duration_ms", 140),
        .volume_pct = json_int(root, "volume_pct", -1),
    };

    if (!g_audio_output_ready) {
        publish_error(request_id, "sound", "speaker not ready");
        cJSON_Delete(root);
        return;
    }
    if (!g_sound_queue || xQueueSend(g_sound_queue, &command, pdMS_TO_TICKS(50)) != pdTRUE) {
        publish_error(request_id, "sound", "sound queue full");
        cJSON_Delete(root);
        return;
    }
    publish_ack(request_id, "sound", "sound queued");
    cJSON_Delete(root);
}

void handle_led_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "led", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    int mode = json_int(root, "mode_id", -1);
    const char* mode_text = json_string(root, "mode");
    if (mode < 0 && mode_text && *mode_text) {
        mode = led_mode_from_string(mode_text);
    }
    if (mode < 0) {
        publish_error(request_id, "led", "unsupported led mode");
        cJSON_Delete(root);
        return;
    }

    queue_led_command(mode,
                      json_int(root, "r", -1),
                      json_int(root, "g", -1),
                      json_int(root, "b", -1));
    publish_ack(request_id, "led", "led set");
    publish_status();
    cJSON_Delete(root);
}

void handle_device_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "device", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    bool changed = false;
    const int volume = json_int(root, "volume_pct", -1);
    const int brightness = json_int(root, "brightness_pct", -1);
    if (volume >= 0) {
        set_speaker_volume_pct(volume);
        changed = true;
    }
    if (brightness >= 0) {
        set_display_brightness_pct(brightness);
        changed = true;
    }
    if (json_bool(root, "display_sleep", false)) {
        set_lcd_sleep(true);
        changed = true;
    }
    if (json_bool(root, "display_wake", false)) {
        set_lcd_sleep(false);
        changed = true;
    }

    if (!changed) {
        publish_error(request_id, "device", "no device setting requested");
    } else {
        publish_ack(request_id, "device", "device set");
        publish_status();
    }
    cJSON_Delete(root);
}

void handle_say_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "say", "invalid json");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    const char* text = json_string(root, "text");
    const char* emotion = json_string(root, "emotion", "speaking");
    if (!text || !*text) {
        publish_error(request_id, "say", "text is required");
        cJSON_Delete(root);
        return;
    }

    UiCommand command = {};
    command.type = UiCommandType::Say;
    command.accent = rgb565(0, 220, 230);
    command.intensity_pct = json_int(root, "intensity_pct", 70);
    command.duration_ms = clamp_int(json_int(root, "duration_ms", 4500), 1000, 15000);
    command.beep = json_bool(root, "beep", true);
    copy_display_text(command.text, sizeof(command.text), text);
    copy_cstr(command.emotion, sizeof(command.emotion), emotion);
    if (!enqueue_ui_command(command)) {
        publish_error(request_id, "say", "ui queue full");
        cJSON_Delete(root);
        return;
    }

    publish_ack(request_id, "say", "text queued");
    cJSON_Delete(root);
}

void set_recording_state(bool enabled, const char* source, const char* request_id, const char* reason)
{
    if (enabled) {
        if (g_recording) {
            publish_status();
            return;
        }
        if (!is_transient_face_emotion(g_face_emotion) && std::strcmp(g_face_emotion, "speaking") != 0) {
            copy_cstr(g_pre_recording_face_emotion,
                      sizeof(g_pre_recording_face_emotion),
                      g_face_emotion);
            g_pre_recording_face_intensity_pct = g_face_intensity_pct;
        } else {
            copy_cstr(g_pre_recording_face_emotion, sizeof(g_pre_recording_face_emotion), "neutral");
            g_pre_recording_face_intensity_pct = 60;
        }
        reset_voice_meter();
        g_face_extra_mode = FaceExtraMode::VoiceWaveform;
        g_recording = true;
        g_recording_started_ms = esp_timer_get_time() / 1000;
        copy_cstr(g_recording_source, sizeof(g_recording_source), source && *source ? source : "manual");
        copy_cstr(g_ui_mode, sizeof(g_ui_mode), "recording");
        set_lcd_sleep(false);
        draw_face("speaking", 70);
        publish_event("recording_started", g_recording_source, request_id, reason);
    } else {
        if (!g_recording) {
            publish_status();
            return;
        }
        char previous_source[sizeof(g_recording_source)] = {};
        copy_cstr(previous_source, sizeof(previous_source), g_recording_source);
        g_recording = false;
        g_recording_started_ms = 0;
        g_face_extra_mode = FaceExtraMode::None;
        reset_voice_meter();
        copy_cstr(g_recording_source, sizeof(g_recording_source), "none");
        copy_cstr(g_ui_mode, sizeof(g_ui_mode), "face");
        copy_face_emotion(g_pre_recording_face_emotion, g_pre_recording_face_intensity_pct);
        draw_face(g_face_emotion, g_face_intensity_pct);
        publish_event("recording_stopped", previous_source, request_id, reason);
    }
    publish_status();
}

void handle_audio_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "audio", "invalid json");
        return;
    }
    const char* request_id = json_string(root, "request_id");
    const char* action = json_string(root, "action");

    if (std::strcmp(action, "set_wakeword") == 0) {
        g_wakeword_enabled = json_bool(root, "enabled", true);
        const char* wakeword = json_string(root, "wakeword", g_wakeword);
        if (wakeword && *wakeword) {
            copy_cstr(g_wakeword, sizeof(g_wakeword), wakeword);
        }
        publish_ack(request_id, "audio", g_wakeword_enabled ? "wakeword enabled" : "wakeword disabled");
        publish_status();
    } else if (std::strcmp(action, "start_recording") == 0) {
        if (!g_audio_input_ready || !g_audio_input) {
            publish_error(request_id, "audio", "microphone not ready");
            cJSON_Delete(root);
            return;
        }
        const char* source = json_string(root, "source", "manual");
        g_recording_min_ms = clamp_int(json_int(root, "min_ms", static_cast<int>(g_recording_min_ms)), 0, 30000);
        g_recording_silence_timeout_ms = clamp_int(json_int(root, "silence_timeout_ms", static_cast<int>(g_recording_silence_timeout_ms)), 0, 10000);
        g_recording_max_ms = clamp_int(json_int(root, "max_ms", static_cast<int>(g_recording_max_ms)), 1000, 60000);
        set_recording_state(true, source, request_id, "start requested");
        publish_ack(request_id, "audio", "recording started");
    } else if (std::strcmp(action, "stop_recording") == 0) {
        const char* source = json_string(root, "source", g_recording_source);
        const char* reason = json_string(root, "reason", "stop requested");
        set_recording_state(false, source, request_id, reason);
        publish_ack(request_id, "audio", "recording stopped");
    } else if (std::strcmp(action, "simulate_wakeword") == 0) {
        if (!g_audio_input_ready || !g_audio_input) {
            publish_error(request_id, "audio", "microphone not ready");
            cJSON_Delete(root);
            return;
        }
        const char* wakeword = json_string(root, "wakeword", g_wakeword);
        publish_event("wakeword_detected", wakeword, request_id, "simulated wakeword");
        g_wakeword_enabled = true;
        set_recording_state(true, "wakeword", request_id, "wakeword detected");
        publish_ack(request_id, "audio", "wakeword simulated");
    } else {
        publish_error(request_id, "audio", "unsupported action");
    }
    cJSON_Delete(root);
}

void handle_system_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "system", "invalid json");
        return;
    }
    const char* request_id = json_string(root, "request_id");
    const char* action = json_string(root, "action");

    if (std::strcmp(action, "ping") == 0) {
        publish_ack(request_id, "system", "pong");
    } else if (std::strcmp(action, "status") == 0) {
        publish_ack(request_id, "system", "status published");
        publish_status();
    } else if (std::strcmp(action, "reboot") == 0) {
        publish_ack(request_id, "system", "rebooting");
        cJSON_Delete(root);
        vTaskDelay(pdMS_TO_TICKS(250));
        esp_restart();
        return;
    } else if (std::strcmp(action, "display_sleep") == 0) {
        set_lcd_sleep(true);
        publish_ack(request_id, "system", "display sleeping");
        publish_status();
    } else if (std::strcmp(action, "display_wake") == 0) {
        set_lcd_sleep(false);
        publish_ack(request_id, "system", "display awake");
        publish_status();
    } else {
        publish_error(request_id, "system", "unsupported action");
    }
    cJSON_Delete(root);
}

esp_err_t http_event_handler(esp_http_client_event_t* evt)
{
    if (evt->event_id != HTTP_EVENT_ON_DATA || !evt->user_data || !evt->data || evt->data_len <= 0) {
        return ESP_OK;
    }
    auto* response = static_cast<HttpResponseBuffer*>(evt->user_data);
    const int space = static_cast<int>(sizeof(response->data)) - response->len - 1;
    if (space <= 0) {
        response->truncated = true;
        return ESP_OK;
    }
    const int copy = std::min(space, evt->data_len);
    if (copy < evt->data_len) {
        response->truncated = true;
    }
    std::memcpy(response->data + response->len, evt->data, copy);
    response->len += copy;
    response->data[response->len] = '\0';
    return ESP_OK;
}

bool post_wav_to_bridge(const uint8_t* wav, size_t wav_size, const char* request_id, const char* source)
{
    if (!wav || wav_size <= kWavHeaderBytes) {
        return false;
    }
    if (std::strlen(CONFIG_STACKCHAN_BRIDGE_AUDIO_URL) == 0) {
        ESP_LOGW(kTag, "voice upload skipped: CONFIG_STACKCHAN_BRIDGE_AUDIO_URL is empty");
        publish_event("audio_upload_skipped", source, request_id, "bridge audio url empty");
        return false;
    }
    if (!wait_for_wifi(pdMS_TO_TICKS(5000))) {
        ESP_LOGW(kTag, "voice upload skipped: wifi not connected");
        publish_event("audio_upload_failed", source, request_id, "wifi not connected");
        return false;
    }

    auto response = std::make_unique<HttpResponseBuffer>();
    esp_http_client_config_t config = {};
    config.url = CONFIG_STACKCHAN_BRIDGE_AUDIO_URL;
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = 45000;
    config.disable_auto_redirect = true;
    config.event_handler = http_event_handler;
    config.user_data = response.get();

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGW(kTag, "voice upload failed: http client init");
        publish_event("audio_upload_failed", source, request_id, "http init failed");
        return false;
    }

    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_http_client_set_header(client, "Content-Type", "audio/wav"));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_http_client_set_header(client, "X-H2S-Pair-Id", CONFIG_STACKCHAN_PAIR_ID));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_http_client_set_header(client, "X-H2S-StackChan-Id", CONFIG_STACKCHAN_STACKCHAN_ID));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_http_client_set_header(client, "X-H2S-Request-Id", request_id && *request_id ? request_id : ""));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_http_client_set_header(client, "X-H2S-Recording-Source", source && *source ? source : "unknown"));

    ESP_LOGI(kTag,
             "voice upload start: %u bytes -> %s request_id=%s",
             static_cast<unsigned>(wav_size),
             CONFIG_STACKCHAN_BRIDGE_AUDIO_URL,
             request_id && *request_id ? request_id : "");
    publish_event("audio_upload_started", source, request_id, "posting wav to bridge");

    esp_err_t err = esp_http_client_set_post_field(client,
                                                   reinterpret_cast<const char*>(wav),
                                                   static_cast<int>(wav_size));
    const int64_t started_us = esp_timer_get_time();
    if (err == ESP_OK) {
        err = esp_http_client_perform(client);
    }
    const int elapsed_ms = static_cast<int>((esp_timer_get_time() - started_us) / 1000);
    const int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || status < 200 || status >= 300) {
        ESP_LOGW(kTag,
                 "voice upload failed: err=%s status=%d elapsed=%dms response=%s",
                 esp_err_to_name(err),
                 status,
                 elapsed_ms,
                 response->data);
        publish_event("audio_upload_failed", source, request_id, "bridge upload failed");
        return false;
    }

    ESP_LOGI(kTag, "voice upload done: status=%d elapsed=%dms response=%s", status, elapsed_ms, response->data);
    publish_event("audio_upload_done", source, request_id, "bridge accepted wav");
    char tts_url[256] = {};
    if (extract_json_string(response->data, "tts_url", tts_url, sizeof(tts_url))) {
        play_wav_url(tts_url);
    }
    return true;
}

void write_aligned_pcm16(WavPlaybackState& state, const uint8_t* data, int len)
{
    if (!data || len <= 0 || !g_audio_output_ready || !g_audio_output) {
        return;
    }
    if (state.has_pending_byte) {
        uint8_t sample[2] = {state.pending_byte, data[0]};
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(g_audio_output, sample, sizeof(sample)));
        state.has_pending_byte = false;
        ++data;
        --len;
    }
    const int aligned_len = len & ~1;
    if (aligned_len > 0) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(g_audio_output, const_cast<uint8_t*>(data), aligned_len));
        data += aligned_len;
        len -= aligned_len;
    }
    if (len == 1) {
        state.pending_byte = data[0];
        state.has_pending_byte = true;
    }
}

int find_wav_data_offset(const uint8_t* data, int len)
{
    if (!data || len < 12 || std::memcmp(data, "RIFF", 4) != 0 || std::memcmp(data + 8, "WAVE", 4) != 0) {
        return -1;
    }
    for (int i = 12; i + 8 <= len; ++i) {
        if (std::memcmp(data + i, "data", 4) == 0) {
            return i + 8;
        }
    }
    return -1;
}

esp_err_t wav_playback_http_event_handler(esp_http_client_event_t* evt)
{
    if (evt->event_id != HTTP_EVENT_ON_DATA || !evt->user_data || !evt->data || evt->data_len <= 0) {
        return ESP_OK;
    }
    auto* state = static_cast<WavPlaybackState*>(evt->user_data);
    const auto* bytes = static_cast<const uint8_t*>(evt->data);
    int len = evt->data_len;
    if (!state->data_started) {
        const int copy = std::min<int>(len, sizeof(state->header) - state->header_len);
        std::memcpy(state->header + state->header_len, bytes, copy);
        state->header_len += copy;
        const int data_offset = find_wav_data_offset(state->header, state->header_len);
        if (data_offset < 0) {
            return ESP_OK;
        }
        state->data_started = true;
        if (state->header_len > data_offset) {
            write_aligned_pcm16(*state, state->header + data_offset, state->header_len - data_offset);
        }
        bytes += copy;
        len -= copy;
    }
    write_aligned_pcm16(*state, bytes, len);
    return ESP_OK;
}

bool play_wav_url(const char* url)
{
    if (!url || !*url || !g_audio_output_ready || !g_audio_output) {
        return false;
    }
    WavPlaybackState playback = {};
    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_GET;
    config.timeout_ms = 30000;
    config.disable_auto_redirect = true;
    config.event_handler = wav_playback_http_event_handler;
    config.user_data = &playback;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return false;
    }
    ESP_LOGI(kTag, "tts playback start: %s", url);
    const esp_err_t err = esp_http_client_perform(client);
    const int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (playback.has_pending_byte) {
        uint8_t sample[2] = {playback.pending_byte, 0};
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(g_audio_output, sample, sizeof(sample)));
    }
    ESP_LOGI(kTag, "tts playback done: status=%d err=%s", status, esp_err_to_name(err));
    return err == ESP_OK && status >= 200 && status < 300;
}

void dispatch_mqtt_payload(const char* topic, int topic_len, const char* data, int data_len)
{
    if (topic_matches(topic, topic_len, g_topic_display)) {
        handle_display_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_system)) {
        handle_system_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_face)) {
        handle_face_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_move)) {
        handle_move_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_motion)) {
        handle_motion_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_sound)) {
        handle_sound_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_audio)) {
        handle_audio_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_led)) {
        handle_led_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_device)) {
        handle_device_command(data, data_len);
    } else if (topic_matches(topic, topic_len, g_topic_say)) {
        handle_say_command(data, data_len);
    }
}

void handle_mqtt_data_event(const esp_mqtt_event_handle_t event)
{
    if (event->total_data_len <= event->data_len && event->current_data_offset == 0) {
        dispatch_mqtt_payload(event->topic, event->topic_len, event->data, event->data_len);
        return;
    }

    if (event->current_data_offset == 0) {
        g_mqtt_rx_expected_len = event->total_data_len;
        g_mqtt_rx_received_len = 0;
        std::memset(g_mqtt_rx_topic, 0, sizeof(g_mqtt_rx_topic));
        std::memset(g_mqtt_rx_payload, 0, sizeof(g_mqtt_rx_payload));
        if (event->topic_len <= 0 || event->topic_len >= kMaxMqttTopic ||
            event->total_data_len <= 0 || event->total_data_len > kMaxMqttPayload) {
            ESP_LOGW(kTag, "mqtt payload too large or invalid: topic_len=%d total=%d",
                     event->topic_len, event->total_data_len);
            publish_error("", "mqtt", "payload too large");
            g_mqtt_rx_expected_len = 0;
            return;
        }
        std::memcpy(g_mqtt_rx_topic, event->topic, event->topic_len);
        g_mqtt_rx_topic[event->topic_len] = '\0';
    }

    if (g_mqtt_rx_expected_len <= 0 ||
        event->current_data_offset < 0 ||
        event->current_data_offset + event->data_len > kMaxMqttPayload ||
        event->current_data_offset + event->data_len > g_mqtt_rx_expected_len) {
        ESP_LOGW(kTag, "mqtt fragmented payload out of bounds");
        g_mqtt_rx_expected_len = 0;
        g_mqtt_rx_received_len = 0;
        return;
    }

    std::memcpy(g_mqtt_rx_payload + event->current_data_offset, event->data, event->data_len);
    g_mqtt_rx_received_len = std::max(g_mqtt_rx_received_len, event->current_data_offset + event->data_len);
    if (g_mqtt_rx_received_len >= g_mqtt_rx_expected_len) {
        g_mqtt_rx_payload[g_mqtt_rx_expected_len] = '\0';
        dispatch_mqtt_payload(g_mqtt_rx_topic,
                              static_cast<int>(std::strlen(g_mqtt_rx_topic)),
                              g_mqtt_rx_payload,
                              g_mqtt_rx_expected_len);
        g_mqtt_rx_expected_len = 0;
        g_mqtt_rx_received_len = 0;
    }
}

void sound_task(void*)
{
    SoundCommand command = {};
    while (true) {
        if (xQueueReceive(g_sound_queue, &command, portMAX_DELAY) == pdTRUE) {
            if (command.volume_pct >= 0) {
                set_speaker_volume_pct(command.volume_pct);
            }
            play_tone(command.frequency_hz, command.duration_ms);
            publish_status();
        }
    }
}

void ui_task(void*)
{
    UiCommand command = {};
    while (true) {
        if (xQueueReceive(g_ui_queue, &command, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        if (command.type == UiCommandType::Display) {
            draw_word_sequence("HERMES", command.text, command.duration_ms, command.accent);
            publish_status();
            draw_face(g_face_emotion, g_face_intensity_pct);
        } else if (command.type == UiCommandType::Face) {
            if (is_transient_face_emotion(command.emotion)) {
                animate_transient_face(command.emotion, command.intensity_pct);
            } else {
                draw_face(command.emotion, command.intensity_pct);
            }
        } else if (command.type == UiCommandType::Say) {
            copy_face_emotion(command.emotion, command.intensity_pct);
            if (command.beep && g_audio_output_ready && g_sound_queue) {
                SoundCommand sound = {.frequency_hz = 660, .duration_ms = 70, .volume_pct = -1};
                xQueueSend(g_sound_queue, &sound, 0);
            }
            publish_status();
            draw_word_sequence("HERMES", command.text, command.duration_ms, command.accent);
            draw_face(g_face_emotion, g_face_intensity_pct);
        }
        publish_status();
    }
}

void audio_state_task(void*)
{
    static constexpr int kAudioChunkSamples = 512;
    static std::array<int16_t, kAudioChunkSamples> samples = {};
    int64_t session_start_ms = 0;
    int64_t quiet_started_ms = 0;
    int64_t last_draw_ms = 0;
    int64_t last_status_ms = 0;
    bool speech_seen = false;
    int active_chunks = 0;
    uint8_t* wav = nullptr;
    size_t wav_capacity_bytes = 0;
    size_t captured_samples = 0;
    char upload_request_id[24] = {};
    char upload_source[24] = {};
    const int min_speech_chunks = std::max(1, (kAudioSampleRate * kVoiceMinSpeechMs / 1000 + kAudioChunkSamples - 1) /
                                              kAudioChunkSamples);

    while (true) {
        if (!g_recording) {
            if (wav) {
                heap_caps_free(wav);
                wav = nullptr;
            }
            wav_capacity_bytes = 0;
            captured_samples = 0;
            upload_request_id[0] = '\0';
            upload_source[0] = '\0';
            session_start_ms = 0;
            quiet_started_ms = 0;
            last_draw_ms = 0;
            last_status_ms = 0;
            speech_seen = false;
            active_chunks = 0;
            vTaskDelay(pdMS_TO_TICKS(40));
            continue;
        }

        if (session_start_ms != g_recording_started_ms) {
            session_start_ms = g_recording_started_ms;
            quiet_started_ms = 0;
            last_draw_ms = 0;
            last_status_ms = 0;
            speech_seen = false;
            active_chunks = 0;
            reset_voice_meter();
            captured_samples = 0;
            copy_cstr(upload_source, sizeof(upload_source), g_recording_source);
            std::snprintf(upload_request_id,
                          sizeof(upload_request_id),
                          "%08x%08x",
                          static_cast<unsigned>(esp_timer_get_time() & 0xffffffff),
                          static_cast<unsigned>(esp_random()));
            if (wav) {
                heap_caps_free(wav);
                wav = nullptr;
            }
            const size_t max_samples = static_cast<size_t>(kAudioSampleRate) *
                                       static_cast<size_t>(clamp_int(static_cast<int>(g_recording_max_ms), 1000, 60000)) /
                                       1000;
            wav_capacity_bytes = kWavHeaderBytes + max_samples * sizeof(int16_t);
            wav = static_cast<uint8_t*>(heap_caps_malloc(wav_capacity_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
            if (!wav) {
                wav = static_cast<uint8_t*>(heap_caps_malloc(wav_capacity_bytes, MALLOC_CAP_8BIT));
            }
            if (!wav) {
                ESP_LOGW(kTag, "voice monitor stopped: no wav buffer for %u bytes", static_cast<unsigned>(wav_capacity_bytes));
                set_recording_state(false, g_recording_source, "", "audio buffer allocation failed");
                vTaskDelay(pdMS_TO_TICKS(100));
                continue;
            }
            make_wav_header(wav, 0);
            ESP_LOGI(kTag,
                     "voice monitor start: source=%s max=%dms silence=%dms no_voice=%dms wav_cap=%u request_id=%s",
                     g_recording_source,
                     static_cast<int>(g_recording_max_ms),
                     static_cast<int>(g_recording_silence_timeout_ms),
                     kVoiceNoSpeechTimeoutMs,
                     static_cast<unsigned>(wav_capacity_bytes),
                     upload_request_id);
        }

        if (!g_audio_input_ready || !g_audio_input) {
            ESP_LOGW(kTag, "voice monitor stopped: microphone not ready");
            set_recording_state(false, g_recording_source, "", "microphone not ready");
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        const esp_err_t err = esp_codec_dev_read(g_audio_input,
                                                 samples.data(),
                                                 samples.size() * sizeof(int16_t));
        if (err != ESP_OK) {
            ESP_LOGW(kTag, "voice monitor read failed: %s", esp_err_to_name(err));
            set_recording_state(false, g_recording_source, "", "microphone read failed");
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        if (wav && wav_capacity_bytes > kWavHeaderBytes) {
            const size_t max_samples = (wav_capacity_bytes - kWavHeaderBytes) / sizeof(int16_t);
            const size_t remaining = captured_samples < max_samples ? max_samples - captured_samples : 0;
            if (remaining > 0) {
                const size_t copy_samples = std::min(remaining, samples.size());
                auto* pcm = reinterpret_cast<int16_t*>(wav + kWavHeaderBytes);
                std::memcpy(pcm + captured_samples, samples.data(), copy_samples * sizeof(int16_t));
                captured_samples += copy_samples;
            }
        }

        int64_t sum = 0;
        int peak = 0;
        for (const int16_t sample : samples) {
            const int value = sample == INT16_MIN ? INT16_MAX : std::abs(static_cast<int>(sample));
            sum += value;
            peak = std::max(peak, value);
        }
        const int avg = static_cast<int>(sum / static_cast<int>(samples.size()));
        const bool active = avg >= kVoiceSilenceAvgThreshold || peak >= kVoiceSilencePeakThreshold;
        const int avg_level = avg * 100 / 1400;
        const int peak_level = peak * 100 / 7000;
        const int level_pct = clamp_int(std::max(avg_level, peak_level), 0, 100);
        push_voice_level(level_pct, avg, peak, active);

        const int64_t now_ms = esp_timer_get_time() / 1000;
        const int64_t elapsed_ms = now_ms - g_recording_started_ms;
        const bool start_grace_done = elapsed_ms >= kVoiceStartGraceMs;

        if (active && start_grace_done) {
            ++active_chunks;
            if (active_chunks >= min_speech_chunks) {
                speech_seen = true;
            }
            quiet_started_ms = 0;
        } else {
            active_chunks = 0;
            if (speech_seen && quiet_started_ms == 0) {
                quiet_started_ms = now_ms;
            }
        }

        if (now_ms - last_draw_ms >= 80) {
            draw_face("speaking", clamp_int(62 + level_pct / 3, 62, 95));
            last_draw_ms = now_ms;
        }
        if (now_ms - last_status_ms >= 500) {
            publish_status();
            last_status_ms = now_ms;
        }

        if (speech_seen && quiet_started_ms > 0 &&
            now_ms - quiet_started_ms >= static_cast<int64_t>(g_recording_silence_timeout_ms)) {
            ESP_LOGI(kTag,
                     "voice monitor stop: silence elapsed=%lldms avg=%d peak=%d level=%d%%",
                     static_cast<long long>(elapsed_ms),
                     avg,
                     peak,
                     level_pct);
            const size_t actual_pcm_bytes = captured_samples * sizeof(int16_t);
            if (wav && actual_pcm_bytes > 0) {
                make_wav_header(wav, static_cast<uint32_t>(actual_pcm_bytes));
            }
            set_recording_state(false, g_recording_source, "", "voice silence");
            if (wav && actual_pcm_bytes > kAudioSampleRate / 2) {
                draw_wrapped_message("SPRACHE", "SENDE ZUR BRIDGE", rgb565(0, 220, 230));
                post_wav_to_bridge(wav, kWavHeaderBytes + actual_pcm_bytes, upload_request_id, upload_source);
                draw_face(g_face_emotion, g_face_intensity_pct);
                publish_status();
            }
        } else if (!speech_seen && elapsed_ms >= kVoiceNoSpeechTimeoutMs) {
            ESP_LOGI(kTag,
                     "voice monitor stop: no voice elapsed=%lldms avg=%d peak=%d level=%d%%",
                     static_cast<long long>(elapsed_ms),
                     avg,
                     peak,
                     level_pct);
            set_recording_state(false, g_recording_source, "", "no voice");
        } else if (elapsed_ms >= static_cast<int64_t>(g_recording_max_ms)) {
            ESP_LOGI(kTag,
                     "voice monitor stop: max duration elapsed=%lldms avg=%d peak=%d level=%d%%",
                     static_cast<long long>(elapsed_ms),
                     avg,
                     peak,
                     level_pct);
            const size_t actual_pcm_bytes = captured_samples * sizeof(int16_t);
            if (wav && actual_pcm_bytes > 0) {
                make_wav_header(wav, static_cast<uint32_t>(actual_pcm_bytes));
            }
            set_recording_state(false, g_recording_source, "", "max duration");
            if (speech_seen && wav && actual_pcm_bytes > kAudioSampleRate / 2) {
                draw_wrapped_message("SPRACHE", "SENDE ZUR BRIDGE", rgb565(0, 220, 230));
                post_wav_to_bridge(wav, kWavHeaderBytes + actual_pcm_bytes, upload_request_id, upload_source);
                draw_face(g_face_emotion, g_face_intensity_pct);
                publish_status();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

void touch_event_task(void*)
{
    bool last_pressed = false;
    int stable_count = 0;
    bool stable_pressed = false;
    uint8_t last_debug_raw = 0;
    int last_debug_x = -1;
    int last_debug_y = -1;
    int64_t last_debug_ms = 0;
    char active_source[16] = "none";
    while (true) {
        uint8_t head_raw = 0;
        uint8_t display_points = 0;
        int x = -1;
        int y = -1;
        const bool head_pressed = read_head_touch_pressed(&head_raw);
        const bool display_pressed = read_display_touch_pressed(&x, &y, &display_points);
        const bool pressed = head_pressed || display_pressed;
        const uint8_t raw = head_pressed ? head_raw : display_points;
        const char* source = head_pressed ? "head_touch" : (display_pressed ? "display_touch" : active_source);
        g_touch_raw = raw;
        g_touch_x = display_pressed ? x : -1;
        g_touch_y = display_pressed ? y : -1;
        const int64_t now_ms = esp_timer_get_time() / 1000;
        if (raw != last_debug_raw || x != last_debug_x || y != last_debug_y || now_ms - last_debug_ms >= 1000) {
            ESP_LOGI(kTag, "touch sample source=%s head_raw=0x%02x display_points=%u x=%d y=%d pressed=%s stable=%s head_ready=%s display_ready=%s",
                     source,
                     head_raw,
                     static_cast<unsigned>(display_points),
                     x,
                     y,
                     pressed ? "true" : "false",
                     stable_pressed ? "true" : "false",
                     g_head_touch_ready ? "true" : "false",
                     g_display_touch_ready ? "true" : "false");
            last_debug_raw = raw;
            last_debug_x = x;
            last_debug_y = y;
            last_debug_ms = now_ms;
        }
        if (pressed && !stable_pressed) {
            copy_cstr(active_source, sizeof(active_source), source);
        }
        if (pressed == last_pressed) {
            stable_count++;
        } else {
            stable_count = 0;
            last_pressed = pressed;
        }

        if (stable_count >= 2 && pressed != stable_pressed) {
            stable_pressed = pressed;
            g_touch_pressed = pressed;
            publish_touch_event(pressed ? "touch_down" : "touch_up",
                                pressed ? source : active_source,
                                raw,
                                pressed,
                                display_pressed ? x : -1,
                                display_pressed ? y : -1);
            if (pressed) {
                if (g_audio_input_ready && g_audio_input) {
                    set_recording_state(true,
                                        source,
                                        "",
                                        "touch pressed");
                } else {
                    draw_face("error", 80);
                    publish_event("recording_error", source, "", "microphone not ready");
                }
            }
            if (!pressed) {
                copy_cstr(active_source, sizeof(active_source), "none");
            }
            publish_status();
        }
        vTaskDelay(pdMS_TO_TICKS(25));
    }
}

void led_effect_task(void*)
{
    int tick = 0;
    int scanner_pos = 0;
    int scanner_dir = 1;
    while (true) {
        const int mode = static_cast<int>(g_led_mode);
        if (mode <= 0 || !g_neon_ready) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        if (mode == 1) {
            set_neon_range(0, 12,
                           static_cast<uint8_t>(g_led_r),
                           static_cast<uint8_t>(g_led_g),
                           static_cast<uint8_t>(g_led_b));
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        if (mode == 2) {
            for (int i = 0; i < 12; ++i) {
                uint8_t r = 0;
                uint8_t g = 0;
                uint8_t b = 0;
                color_wheel(tick * 5 + i * 21, &r, &g, &b);
                set_neon_pixel_raw(i, r / 2, g / 2, b / 2);
            }
            show_neon_pixels();
        } else if (mode == 3) {
            for (int i = 0; i < 12; ++i) {
                const int distance = std::abs(i - scanner_pos);
                const int level = distance == 0 ? 190 : distance == 1 ? 80 : distance == 2 ? 24 : 0;
                set_neon_pixel_raw(i, static_cast<uint8_t>(level), 0, 0);
            }
            show_neon_pixels();
            scanner_pos += scanner_dir;
            if (scanner_pos <= 0 || scanner_pos >= 11) {
                scanner_dir *= -1;
            }
        } else if (mode == 4) {
            const bool on = ((tick / 4) % 2) == 0;
            set_neon_range(0, 12,
                           on ? static_cast<uint8_t>(g_led_r) : 0,
                           on ? static_cast<uint8_t>(g_led_g) : 0,
                           on ? static_cast<uint8_t>(g_led_b) : 0);
        } else if (mode == 5) {
            const float wave = (std::sin(tick * 0.16f) + 1.0f) * 0.5f;
            const float scale = 0.10f + wave * 0.55f;
            set_neon_range(0, 12,
                           static_cast<uint8_t>(g_led_r * scale),
                           static_cast<uint8_t>(g_led_g * scale),
                           static_cast<uint8_t>(g_led_b * scale));
        } else if (mode == 6) {
            for (int i = 0; i < 12; ++i) {
                uint8_t r = 0;
                uint8_t g = 0;
                uint8_t b = 0;
                color_wheel(static_cast<int>(esp_random() & 0xFF) + tick * 9 + i * 31, &r, &g, &b);
                const int sparkle = 35 + static_cast<int>(esp_random() % 130);
                set_neon_pixel_raw(i,
                                   static_cast<uint8_t>((r * sparkle) / 255),
                                   static_cast<uint8_t>((g * sparkle) / 255),
                                   static_cast<uint8_t>((b * sparkle) / 255));
            }
            show_neon_pixels();
        }

        ++tick;
        vTaskDelay(pdMS_TO_TICKS(mode == 3 ? 70 : mode == 6 ? 95 : 80));
    }
}

void hardware_servo_task(void*)
{
    constexpr ServoAxis yaw = {
        .id = 1,
        .default_zero = 460,
        .angle_min_tenth_deg = -1280,
        .angle_max_tenth_deg = 1280,
        .raw_min = 0,
        .raw_max = 1000,
    };
    constexpr ServoAxis pitch = {
        .id = 2,
        .default_zero = 620,
        .angle_min_tenth_deg = 0,
        .angle_max_tenth_deg = 900,
        .raw_min = 0,
        .raw_max = 1000,
    };

    bool bus_started = false;
    bool servo_powered = false;
    int yaw_pos = axis_zero_position(yaw);
    int pitch_pos = axis_zero_position(pitch);
    update_servo_state_pct(yaw, pitch, yaw_pos, pitch_pos);

    while (true) {
        MotionCommand motion = {};
        const bool has_motion = g_motion_queue && xQueueReceive(g_motion_queue, &motion, 0) == pdTRUE;
        const int yaw_delta = g_pending_yaw_delta;
        const int pitch_delta = g_pending_pitch_delta;
        const int yaw_target_pct = g_pending_yaw_target_pct;
        const int pitch_target_pct = g_pending_pitch_target_pct;
        const bool has_yaw_target = yaw_target_pct >= kYawTargetMinPct && yaw_target_pct <= kYawTargetMaxPct;
        const bool has_pitch_target = pitch_target_pct >= kPitchTargetMinPct && pitch_target_pct <= kPitchTargetMaxPct;

        if (!has_motion && yaw_delta == 0 && pitch_delta == 0 && !has_yaw_target && !has_pitch_target) {
            vTaskDelay(pdMS_TO_TICKS(120));
            continue;
        }

        g_pending_yaw_delta = 0;
        g_pending_pitch_delta = 0;
        if (has_yaw_target) {
            g_pending_yaw_target_pct = 101;
        }
        if (has_pitch_target) {
            g_pending_pitch_target_pct = 101;
        }

        if (!servo_powered) {
            if (!set_servo_vm_power(true)) {
                ESP_LOGW(kTag, "servo power unavailable; movement skipped");
                vTaskDelay(pdMS_TO_TICKS(500));
                continue;
            }
            servo_powered = true;
        }

        if (!bus_started) {
            bus_started = g_servo_bus.begin(UART_NUM_1, 1000000, 6, 7);
            if (!bus_started) {
                ESP_LOGE(kTag, "servo UART init failed");
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
        }

        if (!wait_for_servo_bus(yaw, pitch)) {
            ESP_LOGW(kTag, "servo bus not answering");
            set_servo_vm_power(false);
            servo_powered = false;
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        if (!read_safe_servo_position_or_keep_last(yaw, yaw_pos) ||
            !read_safe_servo_position_or_keep_last(pitch, pitch_pos)) {
            ESP_LOGW(kTag, "servo feedback outside safe limits; movement skipped");
            set_servo_vm_power(false);
            servo_powered = false;
            g_temperature_servo_yaw_c = -1;
            g_temperature_servo_pitch_c = -1;
            continue;
        }
        update_servo_temperatures(yaw, pitch);

        g_servo_bus.EnableTorque(yaw.id, 1);
        g_servo_bus.EnableTorque(pitch.id, 1);
        if (has_motion) {
            execute_motion_command(yaw, pitch, yaw_pos, pitch_pos, motion);
            update_servo_temperatures(yaw, pitch);
            g_servo_bus.EnableTorque(yaw.id, 0);
            g_servo_bus.EnableTorque(pitch.id, 0);
            publish_status();
            continue;
        }

        const int yaw_target = has_yaw_target ? target_pct_to_raw_position(yaw, yaw_target_pct) : yaw_pos + yaw_delta;
        const int pitch_target = has_pitch_target ? target_pct_to_raw_position(pitch, pitch_target_pct) : pitch_pos + pitch_delta;
        ESP_LOGI(kTag, "servo target raw yaw=%d pitch=%d", yaw_target, pitch_target);
        move_axes_toward(yaw, pitch, yaw_pos, pitch_pos, yaw_target, pitch_target);
        update_servo_state_pct(yaw, pitch, yaw_pos, pitch_pos);
        update_servo_temperatures(yaw, pitch);
        g_servo_bus.EnableTorque(yaw.id, 0);
        g_servo_bus.EnableTorque(pitch.id, 0);
        publish_status();
    }
}

void wifi_event_handler(void*, esp_event_base_t event_base, int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (g_wifi_events) {
            xEventGroupClearBits(g_wifi_events, kWifiConnectedBit);
        }
        ESP_LOGW(kTag, "wifi disconnected; reconnecting");
        esp_wifi_connect();
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const auto* event = static_cast<ip_event_got_ip_t*>(event_data);
        ESP_LOGI(kTag, "wifi connected: ip=" IPSTR, IP2STR(&event->ip_info.ip));
        if (g_wifi_events) {
            xEventGroupSetBits(g_wifi_events, kWifiConnectedBit);
        }
    }
}

bool init_wifi()
{
    if (std::strlen(CONFIG_STACKCHAN_WIFI_SSID) == 0) {
        display_error("SET WIFI CONFIG");
        ESP_LOGE(kTag, "CONFIG_STACKCHAN_WIFI_SSID is empty");
        return false;
    }

    g_wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler, nullptr, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                        &wifi_event_handler, nullptr, nullptr));

    wifi_config_t wifi_config = {};
    std::strncpy(reinterpret_cast<char*>(wifi_config.sta.ssid),
                 CONFIG_STACKCHAN_WIFI_SSID,
                 sizeof(wifi_config.sta.ssid));
    std::strncpy(reinterpret_cast<char*>(wifi_config.sta.password),
                 CONFIG_STACKCHAN_WIFI_PASSWORD,
                 sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(kTag, "wifi starting: ssid=%s", CONFIG_STACKCHAN_WIFI_SSID);
    return true;
}

bool wait_for_wifi(TickType_t timeout)
{
    if (!g_wifi_events) {
        return false;
    }
    const EventBits_t bits = xEventGroupWaitBits(g_wifi_events, kWifiConnectedBit,
                                                 pdFALSE, pdFALSE, timeout);
    return (bits & kWifiConnectedBit) != 0;
}

void mqtt_event_handler(void*, esp_event_base_t, int32_t event_id, void* event_data)
{
    const esp_mqtt_event_handle_t event = static_cast<esp_mqtt_event_handle_t>(event_data);
    switch (event_id) {
    case MQTT_EVENT_CONNECTED:
        g_mqtt_connected = true;
        ESP_LOGI(kTag, "mqtt connected");
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_display, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_system, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_face, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_move, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_motion, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_sound, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_audio, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_led, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_device, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_say, 1);
        publish_status();
        draw_face(g_face_emotion, g_face_intensity_pct);
        break;
    case MQTT_EVENT_DISCONNECTED:
        g_mqtt_connected = false;
        ESP_LOGW(kTag, "mqtt disconnected");
        break;
    case MQTT_EVENT_DATA:
        handle_mqtt_data_event(event);
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(kTag, "mqtt error");
        break;
    default:
        break;
    }
}

bool init_mqtt()
{
    if (std::strlen(CONFIG_STACKCHAN_MQTT_URI) == 0) {
        display_error("SET MQTT URI");
        ESP_LOGE(kTag, "CONFIG_STACKCHAN_MQTT_URI is empty");
        return false;
    }

    esp_mqtt_client_config_t config = {};
    config.broker.address.uri = CONFIG_STACKCHAN_MQTT_URI;
    g_mqtt_client = esp_mqtt_client_init(&config);
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(g_mqtt_client, MQTT_EVENT_ANY,
                                                   mqtt_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_mqtt_client_start(g_mqtt_client));
    return true;
}

void status_task(void*)
{
    static constexpr int kPowerPollIntervalMs = 250;
    int64_t last_publish_ms = 0;
    bool last_power_known = false;
    bool last_usb_power_present = false;

    while (true) {
        if (g_mqtt_connected) {
            update_battery_status();
            const int64_t now_ms = esp_timer_get_time() / 1000;
            const bool interval_due = (now_ms - last_publish_ms) >= CONFIG_STACKCHAN_STATUS_INTERVAL_MS;
            const bool power_changed = g_battery_known &&
                                       (!last_power_known || g_usb_power_present != last_usb_power_present);
            if (interval_due || power_changed) {
                publish_status();
                last_publish_ms = now_ms;
                last_power_known = g_battery_known;
                last_usb_power_present = g_usb_power_present;
            }
        } else {
            last_publish_ms = 0;
            last_power_known = false;
        }
        vTaskDelay(pdMS_TO_TICKS(kPowerPollIntervalMs));
    }
}

void init_nvs()
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

} // namespace

extern "C" void app_main()
{
    ESP_LOGI(kTag, "starting MQTT hardware firmware");
    build_topics();
    init_nvs();
    load_device_settings();
    init_power_and_reset_panel();
    init_display();
    init_framebuffer();
    display_boot();
    init_temperature_sensor();
    init_speaker();
    init_microphone();
    g_sound_queue = xQueueCreate(4, sizeof(SoundCommand));
    g_motion_queue = xQueueCreate(3, sizeof(MotionCommand));
    g_ui_queue = xQueueCreate(6, sizeof(UiCommand));
    if (g_sound_queue) {
        xTaskCreate(sound_task, "sound", 8192, nullptr, 3, nullptr);
    }
    if (g_ui_queue) {
        xTaskCreate(ui_task, "ui", 8192, nullptr, 3, nullptr);
    }
    xTaskCreate(hardware_servo_task, "servo_hw", 8192, nullptr, 3, nullptr);
    xTaskCreate(led_effect_task, "led_fx", 2048, nullptr, 2, nullptr);
    xTaskCreate(audio_state_task, "audio_state", 12288, nullptr, 2, nullptr);
    xTaskCreate(touch_event_task, "touch_event", 8192, nullptr, 2, nullptr);

    if (!init_wifi()) {
        return;
    }
    draw_wrapped_message("WIFI", "CONNECTING", rgb565(255, 210, 60));
    if (!wait_for_wifi(pdMS_TO_TICKS(15000))) {
        display_error("WIFI TIMEOUT");
        ESP_LOGE(kTag, "wifi timeout");
        return;
    }

    draw_wrapped_message("MQTT", "CONNECTING", rgb565(255, 210, 60));
    if (!init_mqtt()) {
        return;
    }
    xTaskCreate(status_task, "mqtt_status", 6144, nullptr, 3, nullptr);
}
