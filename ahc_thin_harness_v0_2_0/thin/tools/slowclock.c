// ソルバーから見える時計を 1/k の速さで進める LD_PRELOAD 用の部品。
// ソースを変えずに「時間を k 倍使わせたら」を測る（締切も段階ごとの配分も、すべて同じ比率で伸びる）。
//   gcc -O2 -shared -fPIC -o slowclock.so slowclock.c -ldl
//   SLOWCLOCK_K=10 LD_PRELOAD=./slowclock.so ./solver < in.txt > out.txt
// 効くのは clock_gettime / gettimeofday を使うもの（std::chrono を含む）。rdtsc を直接読むソルバーには効かない。
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

static int (*real_clock_gettime)(clockid_t, struct timespec *);
static double k = 1.0;
static struct timespec base[16];

__attribute__((constructor)) static void init(void) {
    real_clock_gettime = dlsym(RTLD_NEXT, "clock_gettime");
    const char *s = getenv("SLOWCLOCK_K");
    if (s) k = atof(s);
    if (k <= 0) k = 1.0;
    for (int i = 0; i < 16; i++) real_clock_gettime(i, &base[i]);
}

int clock_gettime(clockid_t id, struct timespec *ts) {
    if (!real_clock_gettime) init();
    int r = real_clock_gettime(id, ts);
    if (r != 0 || k == 1.0 || id < 0 || id >= 16) return r;
    long long d = (ts->tv_sec - base[id].tv_sec) * 1000000000LL + (ts->tv_nsec - base[id].tv_nsec);
    long long t = base[id].tv_sec * 1000000000LL + base[id].tv_nsec + (long long)(d / k);
    ts->tv_sec = t / 1000000000LL;
    ts->tv_nsec = t % 1000000000LL;
    return 0;
}

int gettimeofday(struct timeval *restrict tv, void *restrict tz) {
    struct timespec ts;
    int r = clock_gettime(CLOCK_REALTIME, &ts);
    tv->tv_sec = ts.tv_sec;
    tv->tv_usec = ts.tv_nsec / 1000;
    (void)tz;
    return r;
}
