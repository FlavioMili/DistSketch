// OpenMP chunk parallelism plus SIMD rank batches. Normal runs are silent.
#include "include/distribution.hpp"
#include "include/mapped_file.hpp"
#include "include/progress.hpp"
#include <charconv>
#include <cstring>
#include <iostream>
namespace distribution {
template <bool Shuffled>
static void write_text(const Distribution &distribution, MappedFile &output, int threads, u64 chunk,
                       Progress &progress) {
  const i64 tasks = i64((distribution.total + chunk - 1) / chunk);
  std::vector<u64> offsets(size_t(tasks) + 1, 0);
#pragma omp parallel for schedule(dynamic, 1) num_threads(threads)
  for (i64 task = 0; task < tasks; ++task) {
    const u64 first = u64(task) * chunk, last = std::min(first + chunk, distribution.total);
    u64 bytes = 0;
    alignas(64) uint32_t ranks[256];
    for (u64 tile = first; tile < last; tile += 256) {
      const int n = int(std::min<u64>(256, last - tile));
      distribution.fill_ranks(tile, n, ranks, Shuffled);
      for (int i = 0; i < n; ++i)
        bytes += distribution.line_length(ranks[i]);
    }
    offsets[size_t(task) + 1] = bytes;
    progress.tick();
  }
  for (size_t i = 1; i < offsets.size(); ++i)
    offsets[i] += offsets[i - 1];
  if (offsets.back() != output.size)
    fail("Text size calculation mismatch");
#pragma omp parallel for schedule(dynamic, 1) num_threads(threads)
  for (i64 task = 0; task < tasks; ++task) {
    char *out = output.data + offsets[size_t(task)];
    const u64 first = u64(task) * chunk, last = std::min(first + chunk, distribution.total);
    alignas(64) uint32_t ranks[256];
    for (u64 tile = first; tile < last; tile += 256) {
      const int n = int(std::min<u64>(256, last - tile));
      distribution.fill_ranks(tile, n, ranks, Shuffled);
      for (int i = 0; i < n; ++i) {
        const i64 value = distribution.value(ranks[i]);
        out = std::to_chars(out, output.data + offsets[size_t(task) + 1], value).ptr;
        *out++ = '\n';
      }
    }
    progress.tick();
  }
}

template <typename T, bool Shuffled>
static void write_binary(const Distribution &distribution, MappedFile &output, int threads,
                         u64 chunk, Progress &progress) {
  const i64 tasks = i64((distribution.total + chunk - 1) / chunk);
#pragma omp parallel for schedule(dynamic, 1) num_threads(threads)
  for (i64 task = 0; task < tasks; ++task) {
    const u64 first = u64(task) * chunk, last = std::min(first + chunk, distribution.total);
    alignas(64) uint32_t ranks[256];
    for (u64 tile = first; tile < last; tile += 256) {
      const int n = int(std::min<u64>(256, last - tile));
      distribution.fill_ranks(tile, n, ranks, Shuffled);
      for (int i = 0; i < n; ++i) {
        const T value = T(distribution.value(ranks[i]));
        std::memcpy(output.data + (tile + u64(i)) * sizeof(T), &value, sizeof(T));
      }
    }
    progress.tick();
  }
}

}  // namespace distribution

int main(int argc, char **argv) {
  using namespace distribution;
  try {
    if (argc != 10 && argc != 11)
      fail("Internal usage: config output format order threads seed sync force chunk");
    const std::string format = argv[3];
    const bool shuffled = std::string(argv[4]) == "shuffled";
    const int threads = std::stoi(argv[5]);
    const u64 seed = std::stoull(argv[6]);
    const bool sync = std::string(argv[7]) == "1", force = std::string(argv[8]) == "1";
    const u64 chunk = std::stoull(argv[9]);
    if (threads < 1 || threads > 256 || chunk < 1 || chunk > 2000000000ULL)
      fail("Invalid threads/chunk");
    Distribution distribution(argv[1], seed);
    const bool binary = format == "u32" || format == "i64";
    if (!binary && format != "txt" && format != "csv")
      fail("Unknown format");
    const size_t stride = format == "u32" ? 4 : 8;
    const uint16_t endian = 1;
    if (binary && *reinterpret_cast<const uint8_t *>(&endian) != 1)
      fail("Binary writer requires little endian");
    if (format == "u32")
      for (const auto &b : distribution.bins)
        if (b.lo < 0 || u64(b.lo) + b.width - 1 > UINT32_MAX)
          fail("Keys do not fit uint32");
    if (!binary)
      distribution.prepare_text_lengths();
    const u64 tasks = (distribution.total + chunk - 1) / chunk;
    Progress progress(argc == 11 ? argv[10] : nullptr, tasks * (binary ? 1 : 2));
    MappedFile output(argv[2], binary ? distribution.total * stride : distribution.text_bytes(),
                      force);
    if (format == "u32") {
      if (shuffled)
        write_binary<uint32_t, true>(distribution, output, threads, chunk, progress);
      else
        write_binary<uint32_t, false>(distribution, output, threads, chunk, progress);
    } else if (format == "i64") {
      if (shuffled)
        write_binary<i64, true>(distribution, output, threads, chunk, progress);
      else
        write_binary<i64, false>(distribution, output, threads, chunk, progress);
    } else {
      if (shuffled)
        write_text<true>(distribution, output, threads, chunk, progress);
      else
        write_text<false>(distribution, output, threads, chunk, progress);
    }
    progress.set_phase(1);
    if (sync)
      output.flush();
    progress.set_phase(2);
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "Error: " << error.what() << '\n';
    return 1;
  }
}
