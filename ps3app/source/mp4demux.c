#include "mp4demux.h"
#include <stdlib.h>
#include <string.h>

static uint32_t rd32(const uint8_t *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static uint64_t rd64(const uint8_t *p) { return ((uint64_t)rd32(p) << 32) | rd32(p + 4); }
static uint16_t rd16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }

static int seek64(FILE *f, uint64_t off) {
#if defined(__PPU__) || defined(__powerpc64__)
    return fseek(f, (long)off, SEEK_SET);
#else
    return fseeko(f, (off_t)off, SEEK_SET);
#endif
}

/* per-track tables while parsing */
typedef struct {
    uint32_t *stsz; uint32_t nstsz, fixed_size;
    uint64_t *stco; uint32_t nstco;
    uint32_t *stsc; uint32_t nstsc;              /* triplets: first_chunk, samples_per_chunk, desc */
    uint32_t *stts; uint32_t nstts;              /* pairs: count, delta */
    uint32_t *ctts; uint32_t nctts;              /* pairs: count, offset */
    uint32_t *stss; uint32_t nstss;
    char handler[5];
    uint32_t timescale; uint64_t duration;
    Mp4Track t;
    int is_avc, is_aac;
} Trak;

static void trak_free(Trak *k) { free(k->stsz); free(k->stco); free(k->stsc); free(k->stts); free(k->ctts); free(k->stss); }

static uint8_t *load(FILE *f, uint64_t off, uint64_t len) {
    if (len > (64u << 20)) return NULL;
    uint8_t *b = malloc(len ? len : 1);
    if (!b) return NULL;
    if (seek64(f, off) || fread(b, 1, len, f) != len) { free(b); return NULL; }
    return b;
}

/* esds: find DecoderSpecificInfo (tag 5) */
static void parse_esds(const uint8_t *p, const uint8_t *e, Trak *k) {
    p += 4;   /* version/flags */
    while (p + 2 <= e) {
        uint8_t tag = *p++;
        uint32_t len = 0; int n = 0;
        while (p < e && n < 4) { uint8_t c = *p++; len = (len << 7) | (c & 0x7F); n++; if (!(c & 0x80)) break; }
        if (tag == 3) { p += 3; if (p <= e && (p[-1] & 0x80)) p += 2; continue; }      /* ES_ID, flags (+dependsOn) */
        if (tag == 4) { p += 13; continue; }                                               /* DecoderConfig header */
        if (tag == 5) { if (len <= sizeof(k->t.asc) && p + len <= e) { memcpy(k->t.asc, p, len); k->t.asc_len = (int)len; } return; }
        p += len;
    }
}

static void parse_stsd(const uint8_t *p, const uint8_t *e, Trak *k) {
    if (e - p < 8) return;
    uint32_t n = rd32(p + 4); p += 8;
    for (uint32_t i = 0; i < n && p + 8 <= e; i++) {
        uint32_t sz = rd32(p); const uint8_t *q = p + 8, *qe = p + sz;
        if (sz < 8 || qe > e) return;
        if (!memcmp(p + 4, "avc1", 4) || !memcmp(p + 4, "avc3", 4)) {
            k->is_avc = 1;
            k->t.width = rd16(q + 24); k->t.height = rd16(q + 26);
            const uint8_t *c = q + 78;               /* after VisualSampleEntry */
            while (c + 8 <= qe) {
                uint32_t bs = rd32(c); if (bs < 8 || c + bs > qe) break;
                if (!memcmp(c + 4, "avcC", 4)) {
                    const uint8_t *a = c + 8, *ae = c + bs;
                    k->t.profile = a[1]; k->t.level = a[3];
                    k->t.nal_len = (a[4] & 3) + 1;
                    int nsps = a[5] & 31; const uint8_t *x = a + 6;
                    uint8_t buf[2048]; int bl = 0;
                    for (int j = 0; j < nsps && x + 2 <= ae; j++) {
                        int l = rd16(x); x += 2;
                        if (x + l > ae || bl + 4 + l > (int)sizeof(buf)) break;
                        memcpy(buf + bl, "\0\0\0\1", 4); memcpy(buf + bl + 4, x, l); bl += 4 + l; x += l;
                    }
                    int npps = x < ae ? *x++ : 0;
                    for (int j = 0; j < npps && x + 2 <= ae; j++) {
                        int l = rd16(x); x += 2;
                        if (x + l > ae || bl + 4 + l > (int)sizeof(buf)) break;
                        memcpy(buf + bl, "\0\0\0\1", 4); memcpy(buf + bl + 4, x, l); bl += 4 + l; x += l;
                    }
                    k->t.sps_pps = malloc(bl); memcpy(k->t.sps_pps, buf, bl); k->t.sps_pps_len = bl;
                }
                c += bs;
            }
        } else if (!memcmp(p + 4, "mp4a", 4)) {
            k->is_aac = 1;
            k->t.channels = rd16(q + 16); k->t.rate = rd16(q + 24);
            uint16_t ver = rd16(q + 8);
            const uint8_t *c = q + 28 + (ver == 1 ? 16 : ver == 2 ? 36 : 0);
            while (c + 8 <= qe) {
                uint32_t bs = rd32(c); if (bs < 8 || c + bs > qe) break;
                if (!memcmp(c + 4, "esds", 4)) parse_esds(c + 8, c + bs, k);
                c += bs;
            }
        }
        p += sz;
    }
}

static uint32_t *u32tab(const uint8_t *p, const uint8_t *e, uint32_t count, int per) {
    if (count > (16u << 20) || p + (uint64_t)count * per * 4 > e) return NULL;
    uint32_t *t = malloc((size_t)count * per * 4 + 4);
    if (!t) return NULL;
    for (uint32_t i = 0; i < count * per; i++) t[i] = rd32(p + i * 4);
    return t;
}

/* walk boxes inside [p,e) of the moov payload */
static void walk(const uint8_t *p, const uint8_t *e, Trak *k, Trak *traks, int *ntrak) {
    while (p + 8 <= e) {
        uint64_t sz = rd32(p); const uint8_t *body = p + 8;
        if (sz == 1) { if (p + 16 > e) return; sz = rd64(p + 8); body = p + 16; }
        else if (sz == 0) sz = (uint64_t)(e - p);
        if (sz < 8 || p + sz > e) return;
        const uint8_t *be = p + sz;
        const char *t = (const char *)p + 4;
        if (!memcmp(t, "trak", 4)) {
            if (*ntrak < 8) { Trak *n = &traks[(*ntrak)++]; memset(n, 0, sizeof(*n)); walk(body, be, n, traks, ntrak); }
        } else if (!memcmp(t, "mdia", 4) || !memcmp(t, "minf", 4) || !memcmp(t, "stbl", 4)) {
            walk(body, be, k, traks, ntrak);
        } else if (k && !memcmp(t, "mdhd", 4)) {
            if (body[0] == 1) { k->timescale = rd32(body + 20); k->duration = rd64(body + 24); }
            else { k->timescale = rd32(body + 12); k->duration = rd32(body + 16); }
        } else if (k && !memcmp(t, "hdlr", 4)) {
            memcpy(k->handler, body + 8, 4); k->handler[4] = 0;
        } else if (k && !memcmp(t, "stsd", 4)) {
            parse_stsd(body, be, k);
        } else if (k && !memcmp(t, "stsz", 4)) {
            k->fixed_size = rd32(body + 4); k->nstsz = rd32(body + 8);
            if (!k->fixed_size) k->stsz = u32tab(body + 12, be, k->nstsz, 1);
        } else if (k && !memcmp(t, "stco", 4)) {
            k->nstco = rd32(body + 4);
            uint32_t *x = u32tab(body + 8, be, k->nstco, 1);
            if (x) { k->stco = malloc(sizeof(uint64_t) * k->nstco + 8); for (uint32_t i = 0; i < k->nstco; i++) k->stco[i] = x[i]; free(x); }
        } else if (k && !memcmp(t, "co64", 4)) {
            k->nstco = rd32(body + 4);
            if (body + 8 + (uint64_t)k->nstco * 8 <= be) { k->stco = malloc(sizeof(uint64_t) * k->nstco + 8); for (uint32_t i = 0; i < k->nstco; i++) k->stco[i] = rd64(body + 8 + i * 8); }
        } else if (k && !memcmp(t, "stsc", 4)) {
            k->nstsc = rd32(body + 4); k->stsc = u32tab(body + 8, be, k->nstsc, 3);
        } else if (k && !memcmp(t, "stts", 4)) {
            k->nstts = rd32(body + 4); k->stts = u32tab(body + 8, be, k->nstts, 2);
        } else if (k && !memcmp(t, "ctts", 4)) {
            k->nctts = rd32(body + 4); k->ctts = u32tab(body + 8, be, k->nctts, 2);
        } else if (k && !memcmp(t, "stss", 4)) {
            k->nstss = rd32(body + 4); k->stss = u32tab(body + 8, be, k->nstss, 1);
        }
        p += sz;
    }
}

static int build(Trak *k) {
    uint32_t n = k->fixed_size ? k->nstsz : k->nstsz;
    if (!n || !k->stco || !k->stsc || !k->stts || (!k->fixed_size && !k->stsz)) return 0;
    Mp4Sample *s = calloc(n, sizeof(Mp4Sample));
    if (!s) return 0;
    /* offsets: chunks x samples-per-chunk */
    uint32_t si = 0;
    for (uint32_t e = 0; e < k->nstsc && si < n; e++) {
        uint32_t first = k->stsc[e * 3], spc = k->stsc[e * 3 + 1];
        uint32_t last = e + 1 < k->nstsc ? k->stsc[(e + 1) * 3] : k->nstco + 1;
        for (uint32_t c = first; c < last && c <= k->nstco && si < n; c++) {
            uint64_t off = k->stco[c - 1];
            for (uint32_t j = 0; j < spc && si < n; j++) {
                uint32_t sz = k->fixed_size ? k->fixed_size : k->stsz[si];
                s[si].off = off; s[si].size = sz; off += sz; si++;
            }
        }
    }
    n = si;
    /* timing */
    int64_t dts = 0; uint32_t i = 0;
    for (uint32_t e = 0; e < k->nstts && i < n; e++)
        for (uint32_t j = 0; j < k->stts[e * 2] && i < n; j++) { s[i++].dts = dts; dts += k->stts[e * 2 + 1]; }
    i = 0;
    for (uint32_t e = 0; k->ctts && e < k->nctts && i < n; e++)
        for (uint32_t j = 0; j < k->ctts[e * 2] && i < n; j++) s[i++].cts = (int32_t)k->ctts[e * 2 + 1];
    if (k->stss) { for (uint32_t e = 0; e < k->nstss; e++) if (k->stss[e] >= 1 && k->stss[e] <= n) s[k->stss[e] - 1].key = 1; }
    else for (i = 0; i < n; i++) s[i].key = 1;
    k->t.present = 1; k->t.s = s; k->t.count = n; k->t.timescale = k->timescale ? k->timescale : 1000;
    k->t.duration = k->duration;
    return 1;
}

int mp4_open(Mp4 *m, const char *path, char *err, int errlen) {
    memset(m, 0, sizeof(*m));
    m->f = fopen(path, "rb");
    if (!m->f) { snprintf(err, errlen, "open"); return 0; }
    fseek(m->f, 0, SEEK_END); m->size = (uint64_t)ftell(m->f);
    uint64_t pos = 0, moov_off = 0, moov_len = 0;
    uint8_t h[16];
    while (pos + 8 <= m->size) {
        if (seek64(m->f, pos) || fread(h, 1, 16, m->f) < 8) break;
        uint64_t sz = rd32(h), hl = 8;
        if (sz == 1) { sz = rd64(h + 8); hl = 16; }
        else if (sz == 0) sz = m->size - pos;
        if (sz < hl) break;
        if (!memcmp(h + 4, "moov", 4)) { moov_off = pos + hl; moov_len = sz - hl; break; }
        pos += sz;
    }
    if (!moov_len) { snprintf(err, errlen, "no moov"); return 0; }
    uint8_t *mv = load(m->f, moov_off, moov_len);
    if (!mv) { snprintf(err, errlen, "moov read"); return 0; }
    Trak traks[8]; int nt = 0;
    walk(mv, mv + moov_len, NULL, traks, &nt);
    free(mv);
    for (int i = 0; i < nt; i++) {
        Trak *k = &traks[i];
        if (!m->video.present && k->is_avc && build(k)) m->video = k->t;
        else if (!m->audio.present && k->is_aac && k->t.asc_len && build(k)) m->audio = k->t;
        else free(k->t.sps_pps);
        trak_free(k);
    }
    if (!m->video.present) { snprintf(err, errlen, "no H.264 track"); mp4_close(m); return 0; }
    return 1;
}

void mp4_close(Mp4 *m) {
    if (m->f) fclose(m->f);
    free(m->video.s); free(m->audio.s); free(m->video.sps_pps);
    memset(m, 0, sizeof(*m));
}

int mp4_read(Mp4 *m, const Mp4Sample *s, uint8_t *buf) {
    return !seek64(m->f, s->off) && fread(buf, 1, s->size, m->f) == s->size;
}

int mp4_read_fp(FILE *f, const Mp4Sample *s, uint8_t *buf) {
    return f && !seek64(f, s->off) && fread(buf, 1, s->size, f) == s->size;
}
