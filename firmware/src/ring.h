// Sample ring buffer shared by the control task (writer) and the network and
// serial tasks (readers). Every sample is stored here BEFORE it is forwarded.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "types.h"

namespace ring {
// Samples are stored packed (44 B, quantized: see ring.cpp and docs/protocol.md) in
// RING_SAMPLES / RING_CHUNK_SAMPLES separate allocations (A33).
bool init();                       // allocates RING_SAMPLES samples; false = failed (all freed)
size_t bytes();                    // heap used by the buffer
bool selfTest();                   // pack/unpack round trip, run once at boot
void push(const Sample &s);        // control task only; assigns nothing, seq set by caller
bool get(uint32_t seq, Sample *out);
bool latest(Sample *out);
// Range of seqs currently held. Returns false while empty.
bool range(uint32_t *oldest, uint32_t *newest);
}  // namespace ring
