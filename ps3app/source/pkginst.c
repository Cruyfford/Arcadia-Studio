/*
 * In-app PKG installer: extracts a PS3 PKG (retail or debug) into its install folder,
 * the same thing the system Package Manager does.
 * Retail: AES-128-CTR with the public PS3 PKG key, counter = header IV + block.
 * Debug : SHA-1 keystream built from the header digest.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>
#include "pkginst.h"

#ifdef HOST_TEST
#include <openssl/aes.h>
#include <openssl/sha.h>
typedef AES_KEY aes_ctx;
static void aes_setkey(aes_ctx *c, const uint8_t *k) { AES_set_encrypt_key(k, 128, c); }
static void aes_block(aes_ctx *c, const uint8_t *in, uint8_t *out) { AES_encrypt(in, out, c); }
static void sha1(const uint8_t *in, size_t n, uint8_t *out) { SHA1(in, n, out); }
#else
#include <mbedtls/aes.h>
#include <mbedtls/sha1.h>
typedef mbedtls_aes_context aes_ctx;
static void aes_setkey(aes_ctx *c, const uint8_t *k) { mbedtls_aes_init(c); mbedtls_aes_setkey_enc(c, k, 128); }
static void aes_block(aes_ctx *c, const uint8_t *in, uint8_t *out) { mbedtls_aes_crypt_ecb(c, MBEDTLS_AES_ENCRYPT, in, out); }
static void sha1(const uint8_t *in, size_t n, uint8_t *out) { mbedtls_sha1_ret(in, n, out); }
#endif

static const uint8_t PS3_PKG_KEY[16] = { 0x2E, 0x7B, 0x71, 0xD7, 0xC9, 0xC9, 0xA1, 0x4E, 0xA3, 0x22, 0x1F, 0x18, 0x88, 0x28, 0xB8, 0xF8 };

static uint32_t be32(const uint8_t *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static uint64_t be64(const uint8_t *p) { return ((uint64_t)be32(p) << 32) | be32(p + 4); }

typedef struct {
    FILE *f; int retail; uint64_t data_off, data_size;
    uint8_t iv[16], digest[16];
    aes_ctx aes;
} Pkg;

/* xor the keystream for absolute data offset `off` into buf */
static void pkg_crypt(Pkg *p, uint64_t off, uint8_t *buf, size_t len) {
    uint64_t block = off / 16; size_t skip = off % 16, i = 0;
    uint8_t ks[20];
    if (p->retail) {
        uint8_t ctr[16];
        while (i < len) {
            /* ctr = iv + block (128-bit big endian add) */
            uint64_t lo = be64(p->iv + 8), hi = be64(p->iv), nlo = lo + block;
            if (nlo < lo) hi++;
            for (int k = 0; k < 8; k++) { ctr[k] = (uint8_t)(hi >> (56 - 8 * k)); ctr[8 + k] = (uint8_t)(nlo >> (56 - 8 * k)); }
            aes_block(&p->aes, ctr, ks);
            for (size_t k = skip; k < 16 && i < len; k++) buf[i++] ^= ks[k];
            skip = 0; block++;
        }
    } else {
        uint8_t kb[0x40]; memset(kb, 0, sizeof(kb));
        memcpy(kb, p->digest, 8); memcpy(kb + 8, p->digest, 8);
        memcpy(kb + 16, p->digest + 8, 8); memcpy(kb + 24, p->digest + 8, 8);
        while (i < len) {
            for (int k = 0; k < 8; k++) kb[0x38 + k] = (uint8_t)(block >> (56 - 8 * k));
            sha1(kb, sizeof(kb), ks);
            for (size_t k = skip; k < 16 && i < len; k++) buf[i++] ^= ks[k];
            skip = 0; block++;
        }
    }
}

static int pkg_read(Pkg *p, uint64_t off, void *buf, size_t len) {
#ifdef HOST_TEST
    if (fseeko(p->f, (off_t)(p->data_off + off), SEEK_SET)) return 0;
#else
    if (fseek(p->f, (long)(p->data_off + off), SEEK_SET)) return 0;
#endif
    if (fread(buf, 1, len, p->f) != len) return 0;
    pkg_crypt(p, off, buf, len);
    return 1;
}

static void mkdirs(const char *path) {
    char tmp[512]; snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *c = tmp + 1; *c; c++) if (*c == '/') { *c = 0; mkdir(tmp, 0777); *c = '/'; }
    mkdir(tmp, 0777);
}

int pkg_install(const char *path, const char *dest_root, char *dest_out, int dest_len,
                char *err, int errlen, volatile double *done, volatile double *total) {
    Pkg p; memset(&p, 0, sizeof(p));
    uint8_t h[0x80];
    int ok = 0; uint8_t *tbl = NULL, *buf = NULL;
    p.f = fopen(path, "rb");
    if (!p.f) { snprintf(err, errlen, "open pkg"); return 0; }
    if (fread(h, 1, sizeof(h), p.f) != sizeof(h) || memcmp(h, "\x7FPKG", 4)) { snprintf(err, errlen, "not a PKG"); goto out; }
    uint16_t rev = (h[4] << 8) | h[5], type = (h[6] << 8) | h[7];
    if (type != 1) { snprintf(err, errlen, "not a PS3 PKG (type %u)", type); goto out; }
    p.retail = (rev & 0x8000) != 0;
    uint32_t meta_off = be32(h + 8), meta_cnt = be32(h + 12), count = be32(h + 0x14);
    p.data_off = be64(h + 0x20); p.data_size = be64(h + 0x28);
    memcpy(p.digest, h + 0x60, 16); memcpy(p.iv, h + 0x70, 16);
    if (p.retail) aes_setkey(&p.aes, PS3_PKG_KEY);
    char cid[0x31]; memcpy(cid, h + 0x30, 0x30); cid[0x30] = 0;
    if (strlen(cid) < 16) { snprintf(err, errlen, "bad content id"); goto out; }
    char title_id[10]; memcpy(title_id, cid + 7, 9); title_id[9] = 0;

    /* content type from metadata (id 2) */
    uint32_t ctype = 0;
    {
        uint8_t m[8]; uint64_t o = meta_off;
        for (uint32_t i = 0; i < meta_cnt && i < 64; i++) {
            if (fseek(p.f, (long)o, SEEK_SET) || fread(m, 1, 8, p.f) != 8) break;
            uint32_t id = be32(m), sz = be32(m + 4);
            if (id == 2 && sz >= 4) { uint8_t v[4]; if (fread(v, 1, 4, p.f) == 4) ctype = be32(v); }
            o += 8 + sz;
        }
    }
    char root[512];
    if (ctype == 9) snprintf(root, sizeof(root), "%s/theme", dest_root);          /* theme */
    else if (ctype == 0xB) { snprintf(err, errlen, "license pkg not supported"); goto out; }
    else snprintf(root, sizeof(root), "%s/game/%s", dest_root, title_id);
    if (dest_out) snprintf(dest_out, dest_len, "%s", root);

    if (count == 0 || count > 200000) { snprintf(err, errlen, "bad item count"); goto out; }
    tbl = malloc((size_t)count * 32);
    if (!tbl || !pkg_read(&p, 0, tbl, (size_t)count * 32)) { snprintf(err, errlen, "read table"); goto out; }

    double sum = 0;
    for (uint32_t i = 0; i < count; i++) if ((be32(tbl + i * 32 + 24) & 0xFF) != 4) sum += (double)be64(tbl + i * 32 + 16);
    if (total) *total = sum;
    if (done) *done = 0;
    mkdirs(root);

    const size_t BUF = 1 << 20; buf = malloc(BUF);
    if (!buf) { snprintf(err, errlen, "no memory"); goto out; }
    double acc = 0;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t *e = tbl + i * 32;
        uint32_t no = be32(e), ns = be32(e + 4), fl = be32(e + 24);
        uint64_t doff = be64(e + 8), dsz = be64(e + 16);
        char name[512];
        if (ns == 0 || ns >= sizeof(name) || !pkg_read(&p, no, name, ns)) { snprintf(err, errlen, "read name %u", i); goto out; }
        name[ns] = 0;
        if (!strcmp(name, "..") || !strncmp(name, "../", 3) || strstr(name, "/../") || (ns >= 3 && !strcmp(name + ns - 3, "/..")) || name[0] == '/') { snprintf(err, errlen, "bad path"); goto out; }
        char out[1024]; snprintf(out, sizeof(out), "%s/%s", root, name);
        if ((fl & 0xFF) == 4) { mkdirs(out); continue; }
        char dir[1024]; snprintf(dir, sizeof(dir), "%s", out);
        char *sl = strrchr(dir, '/'); if (sl) { *sl = 0; mkdirs(dir); }
        FILE *o = fopen(out, "wb");
        if (!o) { snprintf(err, errlen, "write %s", name); goto out; }
        uint64_t pos = 0;
        while (pos < dsz) {
            size_t n = dsz - pos > BUF ? BUF : (size_t)(dsz - pos);
            if (!pkg_read(&p, doff + pos, buf, n) || fwrite(buf, 1, n, o) != n) { fclose(o); snprintf(err, errlen, "copy %s", name); goto out; }
            pos += n; acc += n; if (done) *done = acc;
        }
        fclose(o);
    }
    ok = 1;
out:
    free(tbl); free(buf);
    if (p.f) fclose(p.f);
    return ok;
}

#ifdef HOST_TEST
int main(int argc, char **argv) {
    char err[256] = "", dest[512]; volatile double d, t;
    int r = pkg_install(argv[1], argv[2], dest, sizeof(dest), err, sizeof(err), &d, &t);
    printf("ok=%d dest=%s err=%s %.0f/%.0f\n", r, dest, err, d, t);
    return !r;
}
#endif
