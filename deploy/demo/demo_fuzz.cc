// Minimal libFuzzer target that crashes on a known magic input.
// Triggers an intentional heap buffer overflow under ASan.
#include <cstdint>
#include <cstddef>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size < 4) {
    return 0;
  }

  // Magic: "BOOM" — included in the seed corpus for fast smoke / PR fuzz.
  if (data[0] == 'B' && data[1] == 'O' && data[2] == 'O' && data[3] == 'M') {
    int *heap = new int[4];
    // Deliberate OOB write — volatile sink prevents the compiler from eliding it.
    volatile int *p = heap;
    p[8] = 0xdeadbeef;
    delete[] heap;
  }

  return 0;
}
