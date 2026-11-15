// Task entry points and the queues between them.
#pragma once

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <stddef.h>

#include "types.h"

extern QueueHandle_t g_cmd_queue;    // serial -> control (Command)
extern QueueHandle_t g_reply_queue;  // control -> serial (Reply)

void controlTask(void *arg);
void netTask(void *arg);
void serialTask(void *arg);

// zero offset loaded from NVS in setup()
extern float g_boot_zero_offset;

// Formats one sample as a single-line JSON object. Returns length, 0 on overflow.
size_t formatSampleJson(const Sample &s, bool replayed, char *buf, size_t n);
