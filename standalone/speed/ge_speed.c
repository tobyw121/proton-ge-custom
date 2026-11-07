/* SPDX-License-Identifier: GPL-3.0-or-later
 * Per-process clock scaling, with explicit ELF-import rebinding for late attachment.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

struct control {
    char magic[8];
    uint64_t pid, start, request, requested_bits, applied, applied_bits, calls;
};
_Static_assert(sizeof(struct control) == 64, "control ABI");
static struct control *control;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int (*real_clock)(clockid_t, struct timespec *);
static pid_t owner;
static int initialized, adjusted;
static char attached_directory[PATH_MAX];
static uint64_t sequence;
static double factor = 1.0;
static long double real_anchor[2], virtual_anchor[2];

static uint64_t bits(double value) { uint64_t out; memcpy(&out, &value, 8); return out; }
static double number(uint64_t value) { double out; memcpy(&out, &value, 8); return out; }
static int raw_clock(clockid_t id, struct timespec *ts) {
    if (real_clock) return real_clock(id, ts);
    return syscall(SYS_clock_gettime, id, ts);
}
static int slot(clockid_t id) {
    if (id == CLOCK_MONOTONIC) return 0;
    if (id == CLOCK_MONOTONIC_RAW) return 1;
    return -1;
}
static clockid_t clock_id(int i) { return i ? CLOCK_MONOTONIC_RAW : CLOCK_MONOTONIC; }
static long double nanos(struct timespec ts) { return (long double)ts.tv_sec * 1000000000.0L + ts.tv_nsec; }

static uint64_t start_ticks(void) {
    char buffer[4096], *tail, *save = NULL, *field;
    int fd = open("/proc/self/stat", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t length = read(fd, buffer, sizeof(buffer)-1);
    close(fd);
    if (length <= 0) return 0;
    buffer[length] = 0;
    tail = strrchr(buffer, ')');
    if (!tail) return 0;
    field = strtok_r(tail + 2, " ", &save);
    for (int index=0; field; index++, field=strtok_r(NULL, " ", &save))
        if (index == 19) return strtoull(field, NULL, 10);
    return 0;
}

/* Called with lock held. Children get an independent control file after fork. */
static void prepare_process(void) {
    owner = getpid();
    sequence = 0; adjusted = 0; factor = 1.0;
    for (int i=0; i<2; i++) {
        struct timespec ts = {0};
        raw_clock(clock_id(i), &ts);
        real_anchor[i] = virtual_anchor[i] = nanos(ts);
    }
    const char *directory = attached_directory[0] ? attached_directory : getenv("GE_WINE_SPEED_DIR");
    uint64_t start = start_ticks();
    if (!directory || !start) return;
    char path[PATH_MAX];
    int length = snprintf(path, sizeof(path), "%s/%ld-%llu.control", directory,
                          (long)owner, (unsigned long long)start);
    if (length <= 0 || (size_t)length >= sizeof(path)) return;
    int fd = open(path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return;
    struct stat info;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
        (info.st_mode & 077) || info.st_nlink != 1 || (info.st_size && info.st_size != sizeof(struct control))) {
        close(fd); return;
    }
    if (ftruncate(fd, sizeof(struct control))) { close(fd); return; }
    void *memory = mmap(NULL, sizeof(struct control), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (memory == MAP_FAILED) { unlink(path); return; }
    control = memory;
    int existing = !memcmp(control->magic, "GESPD01", 8) && control->pid == (uint64_t)owner && control->start == start;
    if (!existing) memset(control, 0, sizeof(*control));
    control->calls = 0;
    control->applied = 0;
    control->applied_bits = bits(1.0);
    control->pid = (uint64_t)owner;
    control->start = start;
    if (!existing) control->requested_bits = bits(1.0);
    memcpy(control->magic, "GESPD01", 8);
}

static void before_fork(void) { pthread_mutex_lock(&lock); }
static void after_fork_parent(void) { pthread_mutex_unlock(&lock); }
static void after_fork_child(void) {
    if (control) munmap(control, sizeof(*control));
    control = NULL; owner = 0; sequence = 0; adjusted = 0; factor = 1.0;
    pthread_mutex_unlock(&lock);
}

__attribute__((constructor)) static void initialize(void) {
    real_clock = (int (*)(clockid_t, struct timespec *))dlsym(RTLD_NEXT, "clock_gettime");
    pthread_mutex_lock(&lock);
    prepare_process();
    pthread_mutex_unlock(&lock);
    pthread_atfork(before_fork, after_fork_parent, after_fork_child);
    __atomic_store_n(&initialized, 1, __ATOMIC_RELEASE);
}

static int scaled_clock_gettime(clockid_t id, struct timespec *ts) {
    int index = slot(id);
    if (!__atomic_load_n(&initialized, __ATOMIC_ACQUIRE) || index < 0)
        return raw_clock(id, ts);
    if (owner != getpid()) {
        pthread_mutex_lock(&lock);
        if (owner != getpid()) prepare_process();
        pthread_mutex_unlock(&lock);
    }
    if (!control) return raw_clock(id, ts);
    __atomic_fetch_add(&control->calls, 1, __ATOMIC_RELAXED);
    uint64_t request = __atomic_load_n(&control->request, __ATOMIC_ACQUIRE);
    if (!__atomic_load_n(&adjusted, __ATOMIC_ACQUIRE) && request == __atomic_load_n(&sequence, __ATOMIC_ACQUIRE))
        return raw_clock(id, ts);
    pthread_mutex_lock(&lock);
    request = __atomic_load_n(&control->request, __ATOMIC_ACQUIRE);
    if (!(request & 1) && request != sequence) {
        uint64_t requested = __atomic_load_n(&control->requested_bits, __ATOMIC_ACQUIRE);
        if (request == __atomic_load_n(&control->request, __ATOMIC_ACQUIRE)) {
            double next = number(requested);
            if (isfinite(next) && next >= 0.05 && next <= 20.0) {
                struct timespec now[2];
                if (!raw_clock(CLOCK_MONOTONIC, &now[0]) && !raw_clock(CLOCK_MONOTONIC_RAW, &now[1])) {
                    for (int i=0; i<2; i++) {
                        long double real_now = nanos(now[i]);
                        virtual_anchor[i] += (real_now-real_anchor[i]) * factor;
                        real_anchor[i] = real_now;
                    }
                    factor = next;
                    __atomic_store_n(&adjusted, 1, __ATOMIC_RELEASE);
                    __atomic_store_n(&sequence, request, __ATOMIC_RELEASE);
                    __atomic_store_n(&control->applied_bits, requested, __ATOMIC_RELEASE);
                    __atomic_store_n(&control->applied, request, __ATOMIC_RELEASE);
                }
            }
        }
    }
    int result = raw_clock(id, ts);
    if (!result) {
        long double value = virtual_anchor[index] + (nanos(*ts)-real_anchor[index]) * factor;
        if (value < 0) value = 0;
        if (value > (long double)LLONG_MAX) value = LLONG_MAX;
        int64_t ns = (int64_t)value;
        ts->tv_sec = ns / 1000000000;
        ts->tv_nsec = ns % 1000000000;
    }
    pthread_mutex_unlock(&lock);
    return result;
}

int clock_gettime(clockid_t id, struct timespec *ts) { return scaled_clock_gettime(id,ts); }

int ge_speed_rebind(void *replacement);
/* Called explicitly after dlopen by the debugger. Returns the number of rebound imports. */
int ge_speed_attach(const char *directory) {
    if (!directory || strlen(directory)>=sizeof(attached_directory)) return -3;
    struct stat info;
    if (lstat(directory,&info) || !S_ISDIR(info.st_mode) || info.st_uid!=geteuid() || (info.st_mode&077)) return -3;
    pthread_mutex_lock(&lock);
    if (control) { pthread_mutex_unlock(&lock); return -4; }
    strcpy(attached_directory,directory);
    prepare_process();
    int ready=control!=NULL;
    pthread_mutex_unlock(&lock);
    if (!ready) return -3;
    return ge_speed_rebind((void *)scaled_clock_gettime);
}

#if __SIZEOF_POINTER__ == 4
/* glibc's 64-bit time ABI used by newer 32-bit applications. */
struct ge_timespec64 { int64_t tv_sec, tv_nsec; };
int __clock_gettime64(clockid_t id, struct ge_timespec64 *ts) {
    struct timespec native;
    int result = scaled_clock_gettime(id, &native);
    if (!result) { ts->tv_sec = native.tv_sec; ts->tv_nsec = native.tv_nsec; }
    return result;
}
#endif
