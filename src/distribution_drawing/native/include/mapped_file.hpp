#pragma once
#include "common.hpp"
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <io.h>
#include <sys/stat.h>
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#endif
namespace distribution {
struct MappedFile {
  MappedFile(const MappedFile &) = delete;
  MappedFile &operator=(const MappedFile &) = delete;
  int fd = -1;
  char *data = nullptr;
  u64 size = 0;
#ifdef _WIN32
  HANDLE mapping = nullptr;
#endif
  MappedFile(const std::string &path, u64 bytes, bool force) : size(bytes) {
    static_assert(sizeof(void *) >= 8, "A 64-bit build is required for large memory maps");
    try {
#ifdef _WIN32
      fd = ::_open(path.c_str(), _O_RDWR | _O_CREAT | _O_BINARY | (force ? _O_TRUNC : _O_EXCL),
                   _S_IREAD | _S_IWRITE);
      if (fd < 0)
        fail(std::string("Open output: ") + std::strerror(errno));
      if (::_chsize_s(fd, size))
        fail("Cannot size output file");
      mapping = CreateFileMappingW(reinterpret_cast<HANDLE>(::_get_osfhandle(fd)), nullptr,
                                   PAGE_READWRITE, 0, 0, nullptr);
      if (!mapping)
        fail("CreateFileMapping failed");
      data = static_cast<char *>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, 0));
      if (!data)
        fail("MapViewOfFile failed");
#else
      fd = ::open(path.c_str(), O_RDWR | O_CREAT | (force ? O_TRUNC : O_EXCL), 0644);
      if (fd < 0)
        fail(std::string("Open output: ") + std::strerror(errno));
#ifdef __linux__
      const int allocation = ::posix_fallocate(fd, 0, off_t(size));
      if (allocation && allocation != EOPNOTSUPP && allocation != ENOSYS)
        fail(std::string("Allocate output: ") + std::strerror(allocation));
#endif
      if (::ftruncate(fd, off_t(size)))
        fail(std::string("Size output: ") + std::strerror(errno));
      void *address = ::mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
      if (address == MAP_FAILED)
        fail(std::string("Map output: ") + std::strerror(errno));
      data = static_cast<char *>(address);
#endif
    } catch (...) {
      release();
      throw;
    }
  }
  void flush() {
#ifdef _WIN32
    if (!FlushViewOfFile(data, 0) || ::_commit(fd))
      fail("Flush mapped output failed");
#else
    if (::msync(data, size, MS_SYNC) || ::fsync(fd))
      fail(std::string("Flush mapped output: ") + std::strerror(errno));
#endif
  }
  void release() {
#ifdef _WIN32
    if (data)
      UnmapViewOfFile(data);
    if (mapping)
      CloseHandle(mapping);
    if (fd >= 0)
      ::_close(fd);
#else
    if (data)
      ::munmap(data, size);
    if (fd >= 0)
      ::close(fd);
#endif
    data = nullptr;
    fd = -1;
  }
  ~MappedFile() { release(); }
};

}  // namespace distribution
