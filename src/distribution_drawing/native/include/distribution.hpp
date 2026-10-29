#pragma once
#include "common.hpp"
#include <algorithm>
#include <fstream>
#include <vector>
namespace distribution {
static inline unsigned text_length(i64 value) {
  const u64 x = u64(value < 0 ? -value : value);
  unsigned digits;
  if (x < 100000000ULL) {
    if (x < 10000ULL)
      digits = x < 100 ? (x < 10 ? 1 : 2) : (x < 1000 ? 3 : 4);
    else
      digits = x < 1000000 ? (x < 100000 ? 5 : 6) : (x < 10000000 ? 7 : 8);
  } else if (x < 1000000000000ULL)
    digits = x < 10000000000ULL ? (x < 1000000000ULL ? 9 : 10) : (x < 100000000000ULL ? 11 : 12);
  else
    digits = x < 100000000000000ULL ? (x < 10000000000000ULL ? 13 : 14)
                                    : (x < 1000000000000000ULL ? 15 : 16);
  return digits + unsigned(value < 0) + 1;
}

struct Product {
  u64 high, low;
  bool operator>(const Product &b) const {
    return high > b.high || (high == b.high && low > b.low);
  }
  bool operator<=(const Product &b) const { return !(*this > b); }
};
static inline Product multiply(u64 a, u64 b) {
#if defined(__SIZEOF_INT128__) && !defined(DD_PORTABLE_MULTIPLY)
  const __uint128_t p = __uint128_t(a) * b;
  return {u64(p >> 64), u64(p)};
#else
  const u64 a0 = uint32_t(a), a1 = a >> 32, b0 = uint32_t(b), b1 = b >> 32;
  const u64 p = a0 * b0, t = a1 * b0 + (p >> 32), m = uint32_t(t) + a0 * b1;
  return {a1 * b1 + (t >> 32) + (m >> 32), (m << 32) | uint32_t(p)};
#endif
}
struct Bin {
  u64 end, count, width;
  i64 lo;
  double slope;
  bool product_fits;
  inline i64 value(u64 local) const {
    u64 offset = u64(double(local) * slope);
    if (product_fits) {
      const u64 numerator = local * width;
      while (offset * count > numerator)
        --offset;
      while ((offset + 1) * count <= numerator)
        ++offset;
      return lo + i64(offset);
    }
    const Product numerator = multiply(local, width);
    while (multiply(offset, count) > numerator)
      offset--;
    while (multiply(offset + 1, count) <= numerator)
      offset++;
    return lo + i64(offset);
  }
  u64 below(i64 key) const {
    if (key <= lo)
      return 0;
    if (key > lo + i64(width) - 1)
      return count;
    u64 a = 0, b = count;
    while (a < b) {
      const u64 mid = (a + b) / 2;
      if (value(mid) < key)
        a = mid + 1;
      else
        b = mid;
    }
    return a;
  }
};
struct Distribution {
  std::vector<unsigned char> line_lengths;
  std::vector<Bin> bins;
  std::vector<uint16_t> lookup;
  u64 total = 0;
  uint32_t mask = 0, shift = 0, mul1 = 0, mul2 = 0, key = 0;
  unsigned lookup_shift = 0;
  Distribution(const std::string &path, u64 seed) {
    std::ifstream in(path);
    i64 minimum, maximum;
    size_t n;
    if (!(in >> minimum >> maximum >> n) || !n || n > 4096 || minimum > maximum)
      fail("Invalid config header");
    if (minimum < -9007199254740991LL || maximum > 9007199254740991LL ||
        maximum - minimum >= 9007199254740991LL)
      fail("Keys exceed the safe integer range");
    const u64 width = u64(maximum - minimum) + 1;
    for (size_t i = 0; i < n; i++) {
      u64 count;
      if (!(in >> count) || count > 2000000000ULL)
        fail("Invalid bin count");
      // Exact boundaries without requiring a nonstandard 128-bit division.
      auto boundary = [&](u64 j) { return j * (width / n) + j * (width % n) / n; };
      const i64 lo = minimum + i64(boundary(i));
      const u64 span = boundary(i + 1) - boundary(i);
      total += count;
      if (count) {
        if (!span)
          fail("Empty key interval with nonzero count");
        bins.push_back(
            {total, count, span, lo, double(span) / double(count), span <= UINT64_MAX / count});
      }
    }
    if (!total || total > 2000000000ULL)
      fail("Total must be between 1 and 2 billion");
    unsigned bits = 0;
    while ((u64(1) << bits) < total)
      bits++;
    bits = std::max(1u, bits);
    mask = uint32_t((u64(1) << bits) - 1);
    shift = std::max(1u, bits / 2);
    auto mix = [](u64 x) {
      x += 0x9e3779b97f4a7c15ULL;
      x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
      x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
      return x ^ (x >> 31);
    };
    key = uint32_t(mix(seed)) & mask;
    mul1 = uint32_t(mix(seed + 1) | 1) & mask;
    mul2 = uint32_t(mix(seed + 2) | 1) & mask;
    lookup_shift = bits > 18 ? bits - 18 : 0;
    lookup.resize(size_t((total - 1) >> lookup_shift) + 1);
    size_t bin = 0;
    for (size_t i = 0; i < lookup.size(); i++) {
      const u64 rank = u64(i) << lookup_shift;
      while (rank >= bins[bin].end)
        bin++;
      lookup[i] = uint16_t(bin);
    }
  }

  void prepare_text_lengths() {
    std::vector<u64> boundaries{0, total};
    auto boundary = [&](i64 key) {
      u64 rank = 0;
      for (const auto &bin : bins)
        rank += bin.below(key);
      boundaries.push_back(rank);
    };
    boundary(0);
    for (i64 power = 10; power <= 1000000000000000LL; power *= 10) {
      boundary(power);
      boundary(1 - power);
    }
    std::sort(boundaries.begin(), boundaries.end());
    boundaries.erase(std::unique(boundaries.begin(), boundaries.end()), boundaries.end());
    line_lengths.resize(lookup.size());
    size_t interval = 0;
    for (size_t cell = 0; cell < lookup.size(); ++cell) {
      const u64 first = u64(cell) << lookup_shift,
                last = std::min(total, u64(cell + 1) << lookup_shift);
      while (boundaries[interval + 1] <= first)
        ++interval;
      line_lengths[cell] = last <= boundaries[interval + 1] ? text_length(value(first)) : 0;
    }
  }
  inline unsigned line_length(u64 rank) const {
    const unsigned length = line_lengths[rank >> lookup_shift];
    return length ? length : text_length(value(rank));
  }
  inline i64 value(u64 rank) const {
    size_t index = lookup[rank >> lookup_shift];
    while (rank >= bins[index].end)
      index++;
    const auto &b = bins[index];
    return b.value(rank - (b.end - b.count));
  }

  void fill_ranks(u64 first, int count, uint32_t *ranks, bool shuffled) const {
    if (!shuffled) {
#pragma omp simd
      for (int i = 0; i < count; ++i)
        ranks[i] = uint32_t(first + u64(i));
      return;
    }
#pragma omp simd
    for (int i = 0; i < count; ++i) {
      uint32_t x = uint32_t(first + u64(i));
      x = ((x ^ key) * mul1) & mask;
      x ^= x >> shift;
      x = (x * mul2) & mask;
      x ^= x >> shift;
      ranks[i] = x;
    }
    for (int i = 0; i < count; ++i)
      while (ranks[i] >= total) {
        uint32_t x = ranks[i];
        x = ((x ^ key) * mul1) & mask;
        x ^= x >> shift;
        x = (x * mul2) & mask;
        x ^= x >> shift;
        ranks[i] = x;
      }
  }
  u64 text_bytes() const {
    u64 bytes = 2 * total;  // At least one digit and one LF per value.
    for (const auto &b : bins) {
      bytes += b.below(0);  // Minus signs.
      for (i64 power = 10; power <= 1000000000000000LL; power *= 10)
        bytes += b.below(1 - power) + b.count - b.below(power);
    }
    return bytes;
  }
};

}  // namespace distribution
