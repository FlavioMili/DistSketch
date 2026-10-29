#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
namespace distribution {
using u64 = uint64_t;
using i64 = int64_t;
[[noreturn]] inline void fail(const std::string &message) { throw std::runtime_error(message); }
}  // namespace distribution
