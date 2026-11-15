// Task entry points and the queues between them.
#pragma once

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <stddef.h>

#include "types.h"

extern QueueHandle_t g_cmd_queue;    // serial -> control (Command)
extern QueueHandle_t g_reply_queue;  // control -> serial (Reply)

// For the stack high-water marks in STATUS (review m9).
extern TaskHandle_t g_control_task;
extern TaskHandle_t g_net_task;
extern TaskHandle_t g_serial_task;

void controlTask(void *arg);
void netTask(void *arg);
void serialTask(void *arg);

// zero offset loaded from NVS in setup()
extern float g_boot_zero_offset;
extern bool g_boot_zero_forced;
// true: a valid, non-forced zero exists in NVS (START is refused otherwise, review C1)
extern bool g_boot_zero_ok;

// esp_reset_reason() read in setup() (review m3), and its name.
extern uint8_t g_reset_reason;
const char *resetReasonName(uint8_t r);

// Formats one sample as a single-line JSON object. Returns length, 0 on overflow.
size_t formatSampleJson(const Sample &s, bool replayed, char *buf, size_t n);
