// Sample ring buffer shared by the control task (writer) and the network and
// serial tasks (readers). Every sample is stored here BEFORE it is forwarded.
#pragma once

#include <stdint.h>

#include "types.h"

namespace ring {
bool init();                       // allocates RING_SAMPLES samples
void push(const Sample &s);        // control task only; assigns nothing, seq set by caller
bool get(uint32_t seq, Sample *out);
bool latest(Sample *out);
// Range of seqs currently held. Returns false while empty.
bool range(uint32_t *oldest, uint32_t *newest);
}  // namespace ring
