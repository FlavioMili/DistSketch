#pragma once
#include "mapped_file.hpp"
#include <atomic>
#include <memory>
#include <new>

namespace distribution {
// Optional shared counters for the local UI. No logging or per-key updates.
class Progress {
  std::unique_ptr<MappedFile> mapping;
  std::atomic<u64> *completed = nullptr;
  std::atomic<u64> *phase = nullptr;

public:
  Progress(const char *path, u64 total) {
    if (!path)
      return;
    mapping.reset(new MappedFile(path, 24, true));
    completed = new (mapping->data) std::atomic<u64>(0);
    std::memcpy(mapping->data + 8, &total, 8);
    phase = new (mapping->data + 16) std::atomic<u64>(0);
  }
  void tick() {
    if (completed)
      completed->fetch_add(1, std::memory_order_relaxed);
  }
  void set_phase(u64 value) {
    if (phase)
      phase->store(value, std::memory_order_relaxed);
  }
};
}  // namespace distribution
