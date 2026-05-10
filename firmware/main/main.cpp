#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>

#include "cJSON.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/spi_master.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_event.h"
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
constexpr uint8_t kAw88298Addr = AW88298_CODEC_DEFAULT_ADDR;
constexpr uint16_t kBlack = 0x0000;
constexpr int kAudioSampleRate = 16000;
constexpr int kDefaultSpeakerVolumePct = 80;
constexpr gpio_num_t kAudioMclk = GPIO_NUM_0;
constexpr gpio_num_t kAudioBclk = GPIO_NUM_34;
constexpr gpio_num_t kAudioWs = GPIO_NUM_33;
constexpr gpio_num_t kAudioDout = GPIO_NUM_13;

esp_lcd_panel_handle_t g_panel = nullptr;
esp_lcd_panel_io_handle_t g_panel_io = nullptr;
i2c_master_bus_handle_t g_i2c_bus = nullptr;
EventGroupHandle_t g_wifi_events = nullptr;
esp_mqtt_client_handle_t g_mqtt_client = nullptr;
bool g_mqtt_connected = false;
uint8_t g_display_brightness_pct = CONFIG_STACKCHAN_DISPLAY_BRIGHTNESS;
bool g_display_sleeping = false;
SCSCL g_servo_bus;
i2s_chan_handle_t g_audio_tx = nullptr;
const audio_codec_data_if_t* g_audio_data_if = nullptr;
const audio_codec_ctrl_if_t* g_audio_out_ctrl_if = nullptr;
const audio_codec_gpio_if_t* g_audio_gpio_if = nullptr;
const audio_codec_if_t* g_audio_out_codec_if = nullptr;
esp_codec_dev_handle_t g_audio_output = nullptr;
bool g_audio_output_ready = false;
int g_speaker_volume_pct = kDefaultSpeakerVolumePct;
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

char g_topic_display[96] = {};
char g_topic_system[96] = {};
char g_topic_face[96] = {};
char g_topic_move[96] = {};
char g_topic_sound[96] = {};
char g_topic_led[96] = {};
char g_topic_device[96] = {};
char g_topic_say[96] = {};
char g_topic_status[96] = {};
char g_topic_ack[96] = {};
char g_topic_error[96] = {};

struct SoundCommand {
    int frequency_hz;
    int duration_ms;
    int volume_pct;
};

QueueHandle_t g_sound_queue = nullptr;

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

int clamp_int(int value, int min_value, int max_value)
{
    return std::max(min_value, std::min(max_value, value));
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
        return i2c_master_transmit(device_, data, sizeof(data), 100);
    }

    esp_err_t try_write(uint8_t reg, const uint8_t* data, size_t len)
    {
        std::array<uint8_t, 16> buffer = {};
        if (len + 1 > buffer.size()) {
            return ESP_ERR_INVALID_SIZE;
        }
        buffer[0] = reg;
        std::memcpy(buffer.data() + 1, data, len);
        return i2c_master_transmit(device_, buffer.data(), len + 1, 100);
    }

    esp_err_t try_read_reg(uint8_t reg, uint8_t& value)
    {
        return i2c_master_transmit_receive(device_, &reg, 1, &value, 1, 100);
    }

private:
    i2c_master_dev_handle_t device_ = nullptr;
};

std::unique_ptr<I2cDevice> g_pmic;
std::unique_ptr<I2cDevice> g_py32;

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

void init_power_and_reset_panel()
{
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
    if (!g_panel || w <= 0 || h <= 0) {
        return;
    }
    x = std::max(0, x);
    y = std::max(0, y);
    w = std::min(w, kWidth - x);
    h = std::min(h, kHeight - y);
    if (w <= 0 || h <= 0) {
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
    int prev_x = cx - half;
    int prev_y = cy;
    for (int x = -half; x <= half; x += 3) {
        const float t = static_cast<float>(x) / static_cast<float>(half);
        const int y = cy + (smile ? 1 : -1) * static_cast<int>((1.0f - t * t) * height);
        draw_line(prev_x, prev_y, cx + x, y, color, 3);
        prev_x = cx + x;
        prev_y = y;
    }
}

void clear(uint16_t color)
{
    draw_rect(0, 0, kWidth, kHeight, color);
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

void draw_wrapped_message(const char* title, const char* message, uint16_t accent)
{
    wake_display_if_needed();
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

void copy_face_emotion(const char* emotion, int intensity_pct)
{
    const char* value = emotion && *emotion ? emotion : "neutral";
    std::strncpy(g_face_emotion, value, sizeof(g_face_emotion) - 1);
    g_face_emotion[sizeof(g_face_emotion) - 1] = '\0';
    g_face_intensity_pct = clamp_int(intensity_pct, 0, 100);
}

void draw_face(const char* emotion, int intensity_pct)
{
    wake_display_if_needed();
    copy_face_emotion(emotion, intensity_pct);

    const uint16_t white = rgb565(245, 250, 255);
    const uint16_t cyan = rgb565(20, 180, 255);
    const uint16_t warm = rgb565(255, 230, 120);
    const uint16_t red = rgb565(255, 55, 70);
    const uint16_t face_color = std::strcmp(g_face_emotion, "angry") == 0 ||
                                        std::strcmp(g_face_emotion, "error") == 0
                                    ? red
                                : std::strcmp(g_face_emotion, "happy") == 0 ||
                                        std::strcmp(g_face_emotion, "love") == 0
                                    ? warm
                                    : white;
    const uint16_t accent = std::strcmp(g_face_emotion, "sleep") == 0 ? rgb565(80, 130, 160) : cyan;
    const int pulse = clamp_int(intensity_pct / 18, 0, 6);
    const int left_x = 105;
    const int right_x = 215;
    const int eye_y = 92;
    const int mouth_y = 154;

    clear(kBlack);

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
        draw_ellipse(left_x, eye_y, 13, 28 + pulse, face_color);
        draw_ellipse(right_x, eye_y, 13, 28 + pulse, face_color);
        draw_mouth_curve(160, mouth_y + 22, 72, 24, false, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "surprised") == 0 || std::strcmp(g_face_emotion, "question") == 0) {
        draw_ellipse(left_x, eye_y, 22 + pulse, 30 + pulse, face_color);
        draw_ellipse(right_x, eye_y, 22 + pulse, 30 + pulse, face_color);
        if (std::strcmp(g_face_emotion, "question") == 0) {
            draw_centered_text(mouth_y - 20, "?", 5, white);
        } else {
            draw_ellipse(160, mouth_y, 20 + pulse, 24 + pulse, white);
        }
        return;
    }

    if (std::strcmp(g_face_emotion, "wink") == 0) {
        draw_line(left_x - 22, eye_y, left_x + 22, eye_y, face_color, 5);
        draw_ellipse(right_x, eye_y, 13 + pulse, 28 + pulse, face_color);
        draw_mouth_curve(160, mouth_y - 8, 62, 22, true, white);
        return;
    }

    if (std::strcmp(g_face_emotion, "speaking") == 0) {
        draw_ellipse(left_x, eye_y, 14 + pulse, 29 + pulse, face_color);
        draw_ellipse(right_x, eye_y, 14 + pulse, 29 + pulse, face_color);
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

    draw_ellipse(left_x, eye_y, 13 + pulse, 28 + pulse, face_color);
    draw_ellipse(right_x, eye_y, 13 + pulse, 28 + pulse, face_color);
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
    esp_err_t err = i2s_new_channel(&chan_cfg, &g_audio_tx, nullptr);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "speaker i2s channel failed: %s", esp_err_to_name(err));
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
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_init_std_mode(g_audio_tx, &std_cfg));
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_enable(g_audio_tx));

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_0,
        .rx_handle = nullptr,
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

void build_topics()
{
    const char* pair_id = CONFIG_STACKCHAN_PAIR_ID;
    std::snprintf(g_topic_display, sizeof(g_topic_display), "hermes-stackchan/%s/cmd/display", pair_id);
    std::snprintf(g_topic_system, sizeof(g_topic_system), "hermes-stackchan/%s/cmd/system", pair_id);
    std::snprintf(g_topic_face, sizeof(g_topic_face), "hermes-stackchan/%s/cmd/face", pair_id);
    std::snprintf(g_topic_move, sizeof(g_topic_move), "hermes-stackchan/%s/cmd/move", pair_id);
    std::snprintf(g_topic_sound, sizeof(g_topic_sound), "hermes-stackchan/%s/cmd/sound", pair_id);
    std::snprintf(g_topic_led, sizeof(g_topic_led), "hermes-stackchan/%s/cmd/led", pair_id);
    std::snprintf(g_topic_device, sizeof(g_topic_device), "hermes-stackchan/%s/cmd/device", pair_id);
    std::snprintf(g_topic_say, sizeof(g_topic_say), "hermes-stackchan/%s/cmd/say", pair_id);
    std::snprintf(g_topic_status, sizeof(g_topic_status), "hermes-stackchan/%s/status", pair_id);
    std::snprintf(g_topic_ack, sizeof(g_topic_ack), "hermes-stackchan/%s/ack", pair_id);
    std::snprintf(g_topic_error, sizeof(g_topic_error), "hermes-stackchan/%s/error", pair_id);
}

bool topic_matches(const esp_mqtt_event_handle_t event, const char* expected)
{
    return event->topic_len == static_cast<int>(std::strlen(expected)) &&
           std::strncmp(event->topic, expected, event->topic_len) == 0;
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

void publish_status()
{
    int rssi = 0;
    wifi_ap_record_t ap = {};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        rssi = ap.rssi;
    }

    char payload[900] = {};
    std::snprintf(payload,
                  sizeof(payload),
                  "{\"schema_version\":\"1.0\",\"pair_id\":\"%s\",\"stackchan_id\":\"%s\","
                  "\"uptime_ms\":%lld,\"wifi_rssi\":%d,\"battery_pct\":-1,\"charging\":false,"
                  "\"volume_pct\":%d,\"brightness_pct\":%u,\"display_sleeping\":%s,"
                  "\"wakeword_enabled\":false,\"recording\":false,\"speaking\":false,"
                  "\"head\":{\"pan_pct\":%d,\"tilt_pct\":%d,\"ready\":%s},"
                  "\"led\":{\"mode\":\"%s\",\"mode_id\":%d,\"r\":%d,\"g\":%d,\"b\":%d,\"ready\":%s},"
                  "\"face\":{\"emotion\":\"%s\",\"intensity_pct\":%d},"
                  "\"speaker\":{\"ready\":%s,\"volume_pct\":%d},"
                  "\"camera_available\":false,\"firmware_version\":\"1.0.0-mqtt-hardware\"}",
                  CONFIG_STACKCHAN_PAIR_ID,
                  CONFIG_STACKCHAN_STACKCHAN_ID,
                  static_cast<long long>(esp_timer_get_time() / 1000),
                  rssi,
                  g_speaker_volume_pct,
                  static_cast<unsigned>(g_display_brightness_pct),
                  g_display_sleeping ? "true" : "false",
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
                  g_audio_output_ready ? "true" : "false",
                  g_speaker_volume_pct);
    publish_json(g_topic_status, payload, 1, 1);
}

void handle_display_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "display", "invalid json");
        display_error("INVALID JSON");
        return;
    }

    const char* request_id = json_string(root, "request_id");
    const char* mode = json_string(root, "mode");
    const char* text = json_string(root, "text");
    if (std::strcmp(mode, "text") != 0 || !text || !*text) {
        publish_error(request_id, "display", "expected mode text and non-empty text");
        display_error("BAD DISPLAY PAYLOAD");
        cJSON_Delete(root);
        return;
    }

    draw_wrapped_message("STACKCHAN", text, rgb565(0, 220, 230));
    publish_ack(request_id, "display", "displayed");
    publish_status();
    cJSON_Delete(root);
}

void handle_face_command(const char* data, int len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) {
        publish_error("", "face", "invalid json");
        draw_face("error", 80);
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
    draw_face(emotion, intensity_pct);
    publish_ack(request_id, "face", "face set");
    publish_status();
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
            yaw_target_pct = 0;
            pitch_target_pct = 0;
        } else if (std::strcmp(direction, "up") == 0) {
            pitch_target_pct = 100;
        } else if (std::strcmp(direction, "down") == 0) {
            pitch_target_pct = -100;
        }
    }

    const bool has_yaw_target = yaw_target_pct >= -100 && yaw_target_pct <= 100;
    const bool has_pitch_target = pitch_target_pct >= -100 && pitch_target_pct <= 100;
    yaw_delta = clamp_int(yaw_delta, -80, 80);
    pitch_delta = clamp_int(pitch_delta, -80, 80);

    if (yaw_delta == 0 && pitch_delta == 0 && !has_yaw_target && !has_pitch_target) {
        publish_error(request_id, "move", "no movement requested");
        cJSON_Delete(root);
        return;
    }

    g_pending_yaw_delta += yaw_delta;
    g_pending_pitch_delta += pitch_delta;
    if (has_yaw_target) {
        g_pending_yaw_target_pct = clamp_int(yaw_target_pct, -100, 100);
    }
    if (has_pitch_target) {
        g_pending_pitch_target_pct = clamp_int(pitch_target_pct, -100, 100);
    }
    publish_ack(request_id, "move", "movement queued");
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

    draw_wrapped_message("STACKCHAN", text, rgb565(0, 220, 230));
    copy_face_emotion(emotion, json_int(root, "intensity_pct", 70));
    if (json_bool(root, "beep", true) && g_audio_output_ready && g_sound_queue) {
        SoundCommand command = {.frequency_hz = 660, .duration_ms = 70, .volume_pct = -1};
        xQueueSend(g_sound_queue, &command, 0);
    }
    publish_ack(request_id, "say", "text displayed");
    publish_status();
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
    int64_t last_servo_activity_us = 0;
    int yaw_pos = axis_zero_position(yaw);
    int pitch_pos = axis_zero_position(pitch);
    update_servo_state_pct(yaw, pitch, yaw_pos, pitch_pos);

    while (true) {
        const int yaw_delta = g_pending_yaw_delta;
        const int pitch_delta = g_pending_pitch_delta;
        const int yaw_target_pct = g_pending_yaw_target_pct;
        const int pitch_target_pct = g_pending_pitch_target_pct;
        const bool has_yaw_target = yaw_target_pct >= -100 && yaw_target_pct <= 100;
        const bool has_pitch_target = pitch_target_pct >= -100 && pitch_target_pct <= 100;

        if (yaw_delta == 0 && pitch_delta == 0 && !has_yaw_target && !has_pitch_target) {
            const int64_t now_us = esp_timer_get_time();
            if (servo_powered && last_servo_activity_us > 0 && now_us - last_servo_activity_us > 8LL * 1000 * 1000) {
                set_servo_vm_power(false);
                servo_powered = false;
            }
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
            continue;
        }

        g_servo_bus.EnableTorque(yaw.id, 1);
        g_servo_bus.EnableTorque(pitch.id, 1);
        const int yaw_target = has_yaw_target ? target_pct_to_raw_position(yaw, yaw_target_pct) : yaw_pos + yaw_delta;
        const int pitch_target = has_pitch_target ? target_pct_to_raw_position(pitch, pitch_target_pct) : pitch_pos + pitch_delta;
        ESP_LOGI(kTag, "servo target raw yaw=%d pitch=%d", yaw_target, pitch_target);
        move_axes_toward(yaw, pitch, yaw_pos, pitch_pos, yaw_target, pitch_target);
        update_servo_state_pct(yaw, pitch, yaw_pos, pitch_pos);
        g_servo_bus.EnableTorque(yaw.id, 0);
        g_servo_bus.EnableTorque(pitch.id, 0);
        last_servo_activity_us = esp_timer_get_time();
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
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_sound, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_led, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_device, 1);
        esp_mqtt_client_subscribe(g_mqtt_client, g_topic_say, 1);
        publish_status();
        draw_wrapped_message("MQTT", "CONNECTED", rgb565(40, 255, 120));
        break;
    case MQTT_EVENT_DISCONNECTED:
        g_mqtt_connected = false;
        ESP_LOGW(kTag, "mqtt disconnected");
        break;
    case MQTT_EVENT_DATA:
        if (topic_matches(event, g_topic_display)) {
            handle_display_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_system)) {
            handle_system_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_face)) {
            handle_face_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_move)) {
            handle_move_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_sound)) {
            handle_sound_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_led)) {
            handle_led_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_device)) {
            handle_device_command(event->data, event->data_len);
        } else if (topic_matches(event, g_topic_say)) {
            handle_say_command(event->data, event->data_len);
        }
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
    while (true) {
        if (g_mqtt_connected) {
            publish_status();
        }
        vTaskDelay(pdMS_TO_TICKS(CONFIG_STACKCHAN_STATUS_INTERVAL_MS));
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
    display_boot();
    init_speaker();
    g_sound_queue = xQueueCreate(4, sizeof(SoundCommand));
    if (g_sound_queue) {
        xTaskCreate(sound_task, "sound", 3072, nullptr, 3, nullptr);
    }
    xTaskCreate(hardware_servo_task, "servo_hw", 4096, nullptr, 3, nullptr);
    xTaskCreate(led_effect_task, "led_fx", 2048, nullptr, 2, nullptr);

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
    xTaskCreate(status_task, "mqtt_status", 3072, nullptr, 3, nullptr);
}
