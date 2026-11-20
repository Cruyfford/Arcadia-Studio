/*
 * ARCADIA app for PS3 (PSL1GHT + Tiny3D + FreeType)
 * Reads the menus of the installed ARCADIA store (/dev_hdd0/game/VIDESTORE) and shows them as an app.
 * X on a game asks webMAN MOD to download the package in the background.
 * Text in the store is already shaped (joined Arabic, visual order), so it is drawn left to right as stored.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <malloc.h>
#include <math.h>
#include <sys/process.h>
#include <sys/thread.h>
#include <sys/time.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <net/net.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>
#include <sysmodule/sysmodule.h>
#include <pngdec/pngdec.h>
#include <jpgdec/jpgdec.h>
#include <tiny3d.h>
#include <sys/stat.h>
#include <curl/curl.h>
#include <ft2build.h>
#include FT_FREETYPE_H

#include "ui_strings.h"
#include "pkginst.h"
#include "mp4demux.h"
#include "faad/neaacdec.h"
#ifndef HOST_PREVIEW
#include <codec/vdec.h>
#include <audio/audio.h>
#include <sys/event_queue.h>
#include <ppu-asm.h>
#endif

SYS_PROCESS_PARAM(1001, 0x100000);

#define STORE      "/dev_hdd0/game/VIDESTORE/"
#define STORE_PS3  "/dev_hdd0/game/VIDESTORE/"
#define APPDIR     "/dev_hdd0/game/ARCD00001/"
#define ROOT_XML   STORE "USRDIR/VIDEOGAMESSCZ_xmb.xml"
#define ROOT_VIEW  "VIDEOGAMESSCZ_xmb_main"
#define FOLDER_CLS "type:x-xmb/folder-pixmap"

#define SW 848.0f
#define SH 512.0f

static int running = 1;

/* ------------------------------------------------------------------ textures */
typedef struct { u32 off; u8 *mem; int w, h; int used_w, used_h; } Tex;

static u8 *tex_pool; static u32 tex_used, tex_size;

static u8 *tex_alloc(u32 size) {
    size = (size + 127) & ~127u;
    if (tex_used + size > tex_size) return NULL;
    u8 *p = tex_pool + tex_used; tex_used += size; return p;
}

static void tex_make(Tex *t, int w, int h) {
    t->mem = tex_alloc(w * h * 4); t->w = w; t->h = h; t->used_w = 0; t->used_h = 0;
    t->off = t->mem ? tiny3d_TextureOffset(t->mem) : 0;
}

static void draw_quad(float x, float y, float w, float h, u32 color) {
    tiny3d_SetPolygon(TINY3D_QUADS);
    tiny3d_VertexPos(x, y, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x + w, y, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x + w, y + h, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x, y + h, 65535); tiny3d_VertexColor(color);
    tiny3d_End();
}

static void draw_grad(float x, float y, float w, float h, u32 top, u32 bottom) {
    tiny3d_SetPolygon(TINY3D_QUADS);
    tiny3d_VertexPos(x, y, 65535); tiny3d_VertexColor(top);
    tiny3d_VertexPos(x + w, y, 65535); tiny3d_VertexColor(top);
    tiny3d_VertexPos(x + w, y + h, 65535); tiny3d_VertexColor(bottom);
    tiny3d_VertexPos(x, y + h, 65535); tiny3d_VertexColor(bottom);
    tiny3d_End();
}

static void draw_tex_uv(Tex *t, float x, float y, float w, float h, float u0, float v0, float u1, float v1, u32 color) {
    tiny3d_SetTexture(0, t->off, t->w, t->h, t->w * 4, TINY3D_TEX_FORMAT_A8R8G8B8, TEXTURE_LINEAR);
    tiny3d_SetPolygon(TINY3D_QUADS);
    tiny3d_VertexPos(x, y, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u0, v0);
    tiny3d_VertexPos(x + w, y, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u1, v0);
    tiny3d_VertexPos(x + w, y + h, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u1, v1);
    tiny3d_VertexPos(x, y + h, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u0, v1);
    tiny3d_End();
}

/* draw an image slot keeping its aspect ratio inside the box */
static void draw_image_fit(Tex *t, float x, float y, float bw, float bh, u32 color) {
    if (!t || !t->used_w) return;
    float ar = (float)t->used_w / t->used_h, w = bw, h = bw / ar;
    if (h > bh) { h = bh; w = bh * ar; }
    draw_tex_uv(t, x + (bw - w) / 2, y + (bh - h) / 2, w, h, 0, 0, (float)t->used_w / t->w, (float)t->used_h / t->h, color);
}

static void draw_image_fill(Tex *t, float x, float y, float bw, float bh, u32 color) {
    if (!t || !t->used_w) return;
    draw_tex_uv(t, x, y, bw, bh, 0, 0, (float)t->used_w / t->w, (float)t->used_h / t->h, color);
}

/* ------------------------------------------------------------------ PNG image cache */
#define ICON_SLOTS 64
#define BG_SLOTS   3
typedef struct { char path[256]; Tex t; u32 last; int ok; } ImgSlot;
static ImgSlot icons[ICON_SLOTS], bgs[BG_SLOTS];
static u32 frame_no;

static int load_png_into(const char *path, Tex *t) {
    /* many store images named .png are really JPEG: sniff the header */
    pngData png; memset(&png, 0, sizeof(png));
    unsigned char sig[4] = {0};
    FILE *sf = fopen(path, "rb"); if (!sf) return 0;
    size_t got = fread(sig, 1, 4, sf); fclose(sf);
    if (got >= 3 && sig[0] == 0xFF && sig[1] == 0xD8 && sig[2] == 0xFF) {
        jpgData jd; memset(&jd, 0, sizeof(jd));
        if (jpgLoadFromFile(path, &jd) != 0 || !jd.bmp_out) return 0;
        png.bmp_out = jd.bmp_out; png.pitch = jd.pitch; png.width = jd.width; png.height = jd.height;
    } else if (pngLoadFromFile(path, &png) != 0 || !png.bmp_out) return 0;
    int sw = png.width, sh = png.height;
    float sc = fminf((float)t->w / sw, (float)t->h / sh);
    if (sc > 1.0f) sc = 1.0f;
    int dw = (int)(sw * sc), dh = (int)(sh * sc);
    if (dw < 1) dw = 1;
    if (dh < 1) dh = 1;
    u32 *dst = (u32 *)t->mem;
    for (int y = 0; y < dh; y++) {
        u32 *srow = (u32 *)((u8 *)png.bmp_out + (int)(y / sc) * png.pitch);
        for (int x = 0; x < dw; x++) dst[y * t->w + x] = srow[(int)(x / sc)];
    }
    t->used_w = dw; t->used_h = dh;
    free(png.bmp_out);
    return 1;
}

static const char *ps3_to_path(const char *ps3, char *buf, int n) {
    if (!ps3 || !*ps3) return NULL;
    if (strncmp(ps3, "/dev_", 5) == 0) { snprintf(buf, n, "%s", ps3); return buf; }
    return NULL;
}

static Tex *get_image(ImgSlot *slots, int count, const char *ps3) {
    char path[256];
    if (!ps3_to_path(ps3, path, sizeof(path))) return NULL;
    int lru = 0;
    for (int i = 0; i < count; i++) {
        if (slots[i].path[0] && strcmp(slots[i].path, path) == 0) { slots[i].last = frame_no; return slots[i].ok ? &slots[i].t : NULL; }
        if (slots[i].last < slots[lru].last) lru = i;
    }
    ImgSlot *s = &slots[lru];
    snprintf(s->path, sizeof(s->path), "%s", path); s->last = frame_no;
    s->ok = s->t.mem ? load_png_into(path, &s->t) : 0;
    return s->ok ? &s->t : NULL;
}

/* ------------------------------------------------------------------ text (FreeType glyph atlas) */
#define GLYPH_PX 32
#define ATLAS 2048
#define ATLAS_H 1024
typedef struct { u32 cp; short x, y, w, h, left, top, adv; } Glyph;
static FT_Library ft; static FT_Face face; static int font_ok;
static Tex atlas; static int ax = 1, ay = 1, arow = 0;
static Glyph glyphs[2048]; static int nglyphs;
static u16 gmap[0x10000];           /* codepoint -> glyph index + 1 */
static int atlas_locked;            /* after preload, no more writes to the atlas while the GPU uses it */

static Glyph *glyph(u32 cp) {
    if (cp < 0x10000 && gmap[cp]) return &glyphs[gmap[cp] - 1];
    if (cp >= 0x10000) return NULL;
    if (!font_ok || nglyphs >= 2048 || atlas_locked) return NULL;
    gmap[cp] = nglyphs + 1;
    Glyph *g = &glyphs[nglyphs]; memset(g, 0, sizeof(*g)); g->cp = cp;
    FT_UInt gi = FT_Get_Char_Index(face, cp);
    if (!gi || FT_Load_Glyph(face, gi, FT_LOAD_RENDER)) { g->adv = GLYPH_PX / 3; nglyphs++; return g; }
    FT_GlyphSlot s = face->glyph; FT_Bitmap *b = &s->bitmap;
    if (ax + (int)b->width + 2 > ATLAS) { ax = 1; ay += arow + 2; arow = 0; }
    if (ay + (int)b->rows + 2 > ATLAS_H) { g->adv = s->advance.x >> 6; nglyphs++; return g; }
    u32 *dst = (u32 *)atlas.mem;
    for (int y = 0; y < (int)b->rows; y++)
        for (int x = 0; x < (int)b->width; x++) {
            u8 a = b->buffer[y * b->pitch + x];
            dst[(ay + y) * ATLAS + ax + x] = ((u32)a << 24) | 0x00FFFFFF;
        }
    g->x = ax; g->y = ay; g->w = b->width; g->h = b->rows; g->left = s->bitmap_left; g->top = s->bitmap_top; g->adv = s->advance.x >> 6;
    ax += b->width + 2; if ((int)b->rows > arow) arow = b->rows;
    nglyphs++;
    return g;
}

/* render every glyph the store can use once, before the first frame */
static void preload_glyphs(void) {
    static const u32 ranges[][2] = { {0x20, 0x7E}, {0xA0, 0xFF}, {0x2010, 0x2027}, {0x2030, 0x203A}, {0x2190, 0x2193},
        {0x0600, 0x06FF}, {0xFB50, 0xFBFF}, {0xFE70, 0xFEFF}, {0x0660, 0x0669} };
    for (unsigned r = 0; r < sizeof(ranges) / sizeof(ranges[0]); r++)
        for (u32 c = ranges[r][0]; c <= ranges[r][1]; c++)
            if (FT_Get_Char_Index(face, c)) glyph(c);
    atlas_locked = 1;
}

static u32 utf8_next(const char **p) {
    const u8 *s = (const u8 *)*p; u32 c = *s;
    if (c < 0x80) { *p += 1; return c; }
    if ((c & 0xE0) == 0xC0 && s[1]) { *p += 2; return ((c & 0x1F) << 6) | (s[1] & 0x3F); }
    if ((c & 0xF0) == 0xE0 && s[1] && s[2]) { *p += 3; return ((c & 0x0F) << 12) | ((s[1] & 0x3F) << 6) | (s[2] & 0x3F); }
    if ((c & 0xF8) == 0xF0 && s[1] && s[2] && s[3]) { *p += 4; return ((c & 0x07) << 18) | ((s[1] & 0x3F) << 12) | ((s[2] & 0x3F) << 6) | (s[3] & 0x3F); }
    *p += 1; return '?';
}

static float text_width(const char *s, float size) {
    float sc = size / GLYPH_PX, w = 0;
    while (s && *s) { Glyph *g = glyph(utf8_next(&s)); if (g) w += g->adv * sc; }
    return w;
}

/* draws until max_w (0 = no limit); y is the baseline */
static void draw_text(const char *s, float x, float y, float size, u32 color, float max_w) {
    if (!s) return;
    float sc = size / GLYPH_PX, pen = x;
    tiny3d_SetTexture(0, atlas.off, ATLAS, ATLAS_H, ATLAS * 4, TINY3D_TEX_FORMAT_A8R8G8B8, TEXTURE_LINEAR);
    while (*s) {
        u32 cp = utf8_next(&s);
        if (cp == '\n') break;
        Glyph *g = glyph(cp);
        if (!g) continue;
        if (max_w > 0 && pen + g->adv * sc > x + max_w) break;
        if (g->w && g->h) {
            float gx = pen + g->left * sc, gy = y - g->top * sc, gw = g->w * sc, gh = g->h * sc;
            float u0 = (float)g->x / ATLAS, v0 = (float)g->y / ATLAS_H, u1 = (float)(g->x + g->w) / ATLAS, v1 = (float)(g->y + g->h) / ATLAS_H;
            tiny3d_SetPolygon(TINY3D_QUADS);
            tiny3d_VertexPos(gx, gy, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u0, v0);
            tiny3d_VertexPos(gx + gw, gy, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u1, v0);
            tiny3d_VertexPos(gx + gw, gy + gh, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u1, v1);
            tiny3d_VertexPos(gx, gy + gh, 65535); tiny3d_VertexColor(color); tiny3d_VertexTexture(u0, v1);
            tiny3d_End();
        }
        pen += g->adv * sc;
    }
}

static void draw_text_shadow(const char *s, float x, float y, float size, u32 color, float max_w) {
    draw_text(s, x + 1.2f, y + 1.2f, size, 0x00000099, max_w);
    draw_text(s, x, y, size, color, max_w);
}

/* ------------------------------------------------------------------ store menus (XMBML) */
typedef struct {
    char *key, *title, *info, *icon, *pic, *link, *action;
    char tfile[256], tview[128];
    int folder;
} Node;

typedef struct {
    char file[256], view[128];
    char *title;          /* parent title (shown in the header) */
    char *pic;            /* parent background */
    Node *items; int n, sel, top;
} Level;

#define MAX_DEPTH 16
static Level stack[MAX_DEPTH]; static int depth = -1;

static char *read_file(const char *path, long *len) {
    FILE *f = fopen(path, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc(n + 1); if (!b) { fclose(f); return NULL; }
    if (fread(b, 1, n, f) != (size_t)n) { free(b); fclose(f); return NULL; }
    b[n] = 0; fclose(f); if (len) *len = n; return b;
}

static char *xstrndup(const char *s, int n) { char *d = malloc(n + 1); memcpy(d, s, n); d[n] = 0; return d; }

static void unescape(char *s) {
    char *r = s, *w = s;
    while (*r) {
        if (*r == '&') {
            if (!strncmp(r, "&amp;", 5)) { *w++ = '&'; r += 5; continue; }
            if (!strncmp(r, "&lt;", 4)) { *w++ = '<'; r += 4; continue; }
            if (!strncmp(r, "&gt;", 4)) { *w++ = '>'; r += 4; continue; }
            if (!strncmp(r, "&quot;", 6)) { *w++ = '"'; r += 6; continue; }
            if (!strncmp(r, "&apos;", 6)) { *w++ = '\''; r += 6; continue; }
        }
        *w++ = *r++;
    }
    *w = 0;
}

/* value of attribute name="..." inside [s, e) */
static char *attr(const char *s, const char *e, const char *name) {
    char pat[40]; snprintf(pat, sizeof(pat), "%s=\"", name);
    const char *p = s;
    while ((p = strstr(p, pat)) && p < e) {
        if (p == s || p[-1] == ' ' || p[-1] == '\t' || p[-1] == '\n') {
            p += strlen(pat); const char *q = strchr(p, '"');
            if (!q || q > e) return NULL;
            char *v = xstrndup(p, q - p); unescape(v); return v;
        }
        p++;
    }
    return NULL;
}

/* pair value inside a <Table> block */
static char *pair(const char *s, const char *e, const char *key) {
    char pat[64]; snprintf(pat, sizeof(pat), "<Pair key=\"%s\">", key);
    const char *p = strstr(s, pat);
    if (!p || p > e) return NULL;
    p = strstr(p, "<String>"); if (!p || p > e) return NULL; p += 8;
    const char *q = strstr(p, "</String>"); if (!q || q > e) return NULL;
    char *v = xstrndup(p, q - p); unescape(v); return v;
}

static void resolve(const char *file, const char *src, char *tfile, char *tview) {
    tfile[0] = tview[0] = 0;
    if (!src || !*src) return;
    if (src[0] == '#') { strcpy(tfile, file); snprintf(tview, 128, "%s", src + 1); return; }
    if (!strncmp(src, "xmb://localhost", 15)) {
        const char *path = src + 15, *hash = strchr(path, '#');
        if (!hash) return;
        if (!strncmp(path, STORE_PS3, strlen(STORE_PS3))) {
            snprintf(tfile, 256, "%.*s", (int)(hash - path), path);
            snprintf(tview, 128, "%s", hash + 1);
            FILE *f = fopen(tfile, "rb"); if (f) fclose(f); else tfile[0] = 0;
        }
    }
}

static char *extract_link(char *pkg_src, char *action) {
    if (pkg_src && !strncmp(pkg_src, "http", 4)) return strdup(pkg_src);
    if (action) {
        char *u = strstr(action, "url=");
        if (!strncmp(action, "http://127.0.0.1", 16) && u) return strdup(u + 4);
        if (!strncmp(action, "http", 4) && !strstr(action, "127.0.0.1") && !strstr(action, "localhost")) return strdup(action);
    }
    return NULL;
}

static int load_level(Level *L, const char *file, const char *view) {
    memset(L, 0, sizeof(*L));
    snprintf(L->file, sizeof(L->file), "%s", file); snprintf(L->view, sizeof(L->view), "%s", view);
    char *txt = read_file(file, NULL);
    if (!txt) return 0;
    char pat[200]; snprintf(pat, sizeof(pat), "<View id=\"%s\">", view);
    char *vs = strstr(txt, pat);
    if (!vs) { free(txt); return 0; }
    char *ve = strstr(vs, "</View>"); if (!ve) ve = txt + strlen(txt);
    char *items = strstr(vs, "<Items>"); if (!items || items > ve) items = ve;
    int cap = 32; L->items = calloc(cap, sizeof(Node));
    char *p = items;
    while ((p = strchr(p, '<')) && p < ve) {
        if (strncmp(p, "<Query", 6) && strncmp(p, "<Item", 5)) { p++; continue; }
        if (!strncmp(p, "<Items", 6)) { p++; continue; }
        char *te = strchr(p, '>'); if (!te || te > ve) break;
        char *key = attr(p, te, "key"), *cls = attr(p, te, "class"), *src = attr(p, te, "src");
        p = te + 1;
        if (!key) { free(cls); free(src); continue; }
        /* its table */
        char tpat[200]; snprintf(tpat, sizeof(tpat), "<Table key=\"%s\">", key);
        char *ts = strstr(vs, tpat); char *tend = ts ? strstr(ts, "</Table>") : NULL;
        if (ts && (ts > items || !tend)) { ts = NULL; }
        if (L->n == cap) { cap *= 2; L->items = realloc(L->items, cap * sizeof(Node)); }
        Node *n = &L->items[L->n]; memset(n, 0, sizeof(*n));
        n->key = key;
        if (ts) {
            n->title = pair(ts, tend, "title"); n->info = pair(ts, tend, "info");
            n->icon = pair(ts, tend, "icon"); n->pic = pair(ts, tend, "pic");
            char *pkg = pair(ts, tend, "pkg_src");
            n->action = pair(ts, tend, "module_action");
            n->link = extract_link(pkg, n->action);
            free(pkg);
        }
        if (!n->title) n->title = strdup(key);
        resolve(file, src, n->tfile, n->tview);
        n->folder = n->tfile[0] && cls && !strcmp(cls, FOLDER_CLS);
        if (!n->folder) n->tfile[0] = 0;
        /* skip entries that do nothing here */
        if (!n->folder && !n->link && !(n->action && !strncmp(n->action, "http://127.0.0.1", 16))) {
            free(n->key); free(n->title); free(n->info); free(n->icon); free(n->pic); free(n->link); free(n->action);
        } else {
            L->n++;
        }
        free(cls); free(src);
    }
    free(txt);
    return 1;
}

static void free_level(Level *L) {
    for (int i = 0; i < L->n; i++) {
        Node *n = &L->items[i];
        free(n->key); free(n->title); free(n->info); free(n->icon); free(n->pic); free(n->link); free(n->action);
    }
    free(L->items); free(L->title); free(L->pic); memset(L, 0, sizeof(*L));
}

/* ------------------------------------------------------------------ webMAN */
static char toast[300]; static int toast_frames;
static void show_toast(const char *s) { snprintf(toast, sizeof(toast), "%s", s); toast_frames = 60 * 8; }

/* the request runs on its own thread so the UI never freezes */
static volatile int wm_busy, wm_result;      /* result: 0 none, 1 ok, 2 fail */
static char wm_path[2048]; static const char *wm_ok_msg;
static sys_ppu_thread_t wm_thread;

static int webman_get_blocking(const char *path) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct timeval tv = { 4, 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a; memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET; a.sin_port = htons(80); a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&a, sizeof(a)) < 0) { close(s); return 0; }
    static char req[3072]; char enc[2048]; int j = 0;
    for (const char *c = path; *c && j < (int)sizeof(enc) - 4; c++) {
        if (*c == ' ') { enc[j++] = '%'; enc[j++] = '2'; enc[j++] = '0'; } else enc[j++] = *c;
    }
    enc[j] = 0;
    int n = snprintf(req, sizeof(req), "GET %s HTTP/1.0\r\nHost: 127.0.0.1\r\nUser-Agent: ArcadiaPS3\r\nConnection: close\r\n\r\n", enc);
    int ok = send(s, req, n, 0) == n;
    char buf[64]; if (ok) recv(s, buf, sizeof(buf), 0);   /* bounded by the timeout */
    close(s);
    return ok;
}

static void wm_entry(void *arg) {
    wm_result = webman_get_blocking(wm_path) ? 1 : 2;
    wm_busy = 0;
    sysThreadExit(0);
}

static void webman_async(const char *path, const char *ok_msg) {
    if (wm_busy) { show_toast(S_WAIT); return; }
    snprintf(wm_path, sizeof(wm_path), "%s", path); wm_ok_msg = ok_msg;
    wm_busy = 1; wm_result = 0;
    if (sysThreadCreate(&wm_thread, wm_entry, NULL, 1500, 0x8000, 0, "wm_get") != 0) {
        wm_busy = 0; show_toast(S_NOWM); return;
    }
    show_toast(S_WAIT);
}

static void webman_poll(void) {
    if (wm_result) { show_toast(wm_result == 1 ? wm_ok_msg : S_NOWM); wm_result = 0; }
}

/* ------------------------------------------------------------------ background download queue (no webMAN needed) */
#define PKG_DIR "/dev_hdd0/packages"
#define DL_LIST APPDIR "USRDIR/downloads.txt"
enum { J_QUEUED, J_DOWNLOADING, J_INSTALLING, J_DONE, J_FAILED, J_CANCELED };
#define MAX_JOBS 96
typedef struct {
    volatile int state, cancel, used;
    volatile double now, total, speed;
    char url[1024], title[256], info[160], icon[384], err[160];
    char name[256], final_[512], part[512];
    double last_now; u32 last_frame;
    int notified;
} Job;
static Job jobs[MAX_JOBS];
static volatile int worker_quit, worker_running, jobs_dirty;
static sys_ppu_thread_t worker_thread;
static Job *volatile current_job;

static size_t dl_write(void *p, size_t sz, size_t n, void *f) { return fwrite(p, sz, n, (FILE *)f); }
static int dl_progress(void *u, curl_off_t total, curl_off_t now, curl_off_t ut, curl_off_t un) {
    Job *j = (Job *)u;
    j->total = (double)total; j->now = (double)now;
    return (j->cancel || worker_quit) ? 1 : 0;
}

static void url_filename(const char *url, char *out, int n) {
    const char *q = strchr(url, '?'); int len = q ? (int)(q - url) : (int)strlen(url);
    const char *b = url + len; while (b > url && b[-1] != '/') b--;
    int j = 0;
    for (const char *c = b; c < url + len && j < n - 5; c++) {
        char ch = *c;
        if (ch == '%' && c + 2 < url + len) { char h[3] = { c[1], c[2], 0 }; ch = (char)strtol(h, NULL, 16); c += 2; }
        if (ch == '/' || ch == '\\' || ch == ':' || ch == '"' || ch == '*' || ch == '?' || ch == '<' || ch == '>' || ch == '|') ch = '_';
        out[j++] = ch;
    }
    out[j] = 0;
    if (!j) snprintf(out, n, "arcadia_download");
    if (j < 4 || strcasecmp(out + strlen(out) - 4, ".pkg")) strncat(out, ".pkg", n - strlen(out) - 1);
}

/* fallback DNS: ask public resolvers directly over UDP when the system resolver fails (curl error 6) */
static int dns_query(const char *server, const char *host, char *ip, int iplen) {
    unsigned char q[512], r[512]; int n = 0;
    static unsigned short qid = 0x4172;
    qid++;
    q[n++] = qid >> 8; q[n++] = qid & 255; q[n++] = 1; q[n++] = 0;   /* recursion desired */
    q[n++] = 0; q[n++] = 1; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0; q[n++] = 0;
    const char *h = host;
    while (*h && n < 480) {
        const char *dot = strchr(h, '.'); int l = dot ? (int)(dot - h) : (int)strlen(h);
        if (l <= 0 || l > 63) return 0;
        q[n++] = l; memcpy(q + n, h, l); n += l; h += l; if (*h == '.') h++;
    }
    q[n++] = 0; q[n++] = 0; q[n++] = 1; q[n++] = 0; q[n++] = 1;      /* type A, class IN */
    int s = socket(AF_INET, SOCK_DGRAM, 0); if (s < 0) return 0;
    struct timeval tv = { 3, 0 }; setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a; memset(&a, 0, sizeof(a)); a.sin_family = AF_INET; a.sin_port = htons(53); a.sin_addr.s_addr = inet_addr(server);
    int ok = 0;
    if (sendto(s, q, n, 0, (struct sockaddr *)&a, sizeof(a)) == n) {
        int got = recvfrom(s, r, sizeof(r), 0, NULL, NULL);
        if (got > 12 && r[0] == q[0] && r[1] == q[1]) {
            int an = (r[6] << 8) | r[7], p = 12;
            while (p < got && r[p]) p += r[p] + 1;        /* skip question name */
            p += 5;
            for (int i = 0; i < an && p + 12 <= got && !ok; i++) {
                if ((r[p] & 0xC0) == 0xC0) p += 2; else { while (p < got && r[p]) p += r[p] + 1; p++; }
                if (p + 10 > got) break;
                int type = (r[p] << 8) | r[p + 1], rdl = (r[p + 8] << 8) | r[p + 9]; p += 10;
                if (type == 1 && rdl == 4 && p + 4 <= got) { snprintf(ip, iplen, "%d.%d.%d.%d", r[p], r[p + 1], r[p + 2], r[p + 3]); ok = 1; }
                p += rdl;
            }
        }
    }
    close(s);
    return ok;
}
static int dns_fallback(const char *host, char *ip, int iplen) {
    return dns_query("8.8.8.8", host, ip, iplen) || dns_query("1.1.1.1", host, ip, iplen) || dns_query("9.9.9.9", host, ip, iplen);
}
static void url_host(const char *url, char *host, int n) {
    const char *p = strstr(url, "://"); p = p ? p + 3 : url;
    int l = (int)strcspn(p, ":/?#"); snprintf(host, n, "%.*s", l, p);
}

static void job_run(Job *jb) {
    int state = J_FAILED;
    mkdir(PKG_DIR, 0777);
    FILE *f = fopen(jb->part, "wb");
    snprintf(jb->err, sizeof(jb->err), f ? "curl init" : "open " PKG_DIR " failed");
    CURL *c = f ? curl_easy_init() : NULL;
    jb->now = jb->total = 0;
    if (c) {
        static char fbuf[1 << 20]; setvbuf(f, fbuf, _IOFBF, sizeof(fbuf));
        curl_easy_setopt(c, CURLOPT_URL, jb->url);
        curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(c, CURLOPT_MAXREDIRS, 10L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
        curl_easy_setopt(c, CURLOPT_USERAGENT, "Mozilla/5.0 (PLAYSTATION 3; 4.92)");
        curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, dl_write);
        curl_easy_setopt(c, CURLOPT_WRITEDATA, f);
        curl_easy_setopt(c, CURLOPT_NOPROGRESS, 0L);
        curl_easy_setopt(c, CURLOPT_XFERINFOFUNCTION, dl_progress);
        curl_easy_setopt(c, CURLOPT_XFERINFODATA, jb);
        curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 20L);
        curl_easy_setopt(c, CURLOPT_LOW_SPEED_LIMIT, 1L);
        curl_easy_setopt(c, CURLOPT_LOW_SPEED_TIME, 60L);
        curl_easy_setopt(c, CURLOPT_BUFFERSIZE, 512L * 1024);
        CURLcode r = curl_easy_perform(c);
        /* resolver failed: look the host up ourselves and retry (also for redirect targets) */
        struct curl_slist *resolve = NULL;
        for (int tries = 0; r == CURLE_COULDNT_RESOLVE_HOST && tries < 4 && !jb->cancel && !worker_quit; tries++) {
            char *eff = NULL, host[256], ip[32], entry[320];
            curl_easy_getinfo(c, CURLINFO_EFFECTIVE_URL, &eff);
            url_host(eff ? eff : jb->url, host, sizeof(host));
            if (!host[0] || !dns_fallback(host, ip, sizeof(ip))) break;
            snprintf(entry, sizeof(entry), "%s:80:%s", host, ip); resolve = curl_slist_append(resolve, entry);
            snprintf(entry, sizeof(entry), "%s:443:%s", host, ip); resolve = curl_slist_append(resolve, entry);
            curl_easy_setopt(c, CURLOPT_RESOLVE, resolve);
            r = curl_easy_perform(c);
        }
        long code = 0; curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(c);
        if (resolve) curl_slist_free_all(resolve);
        fclose(f); f = NULL;
        if (r == CURLE_OK && code < 400) {
            unlink(jb->final_);
            if (rename(jb->part, jb->final_) != 0) { snprintf(jb->err, sizeof(jb->err), "rename"); state = J_FAILED; }
            else {
                jb->state = J_INSTALLING; jb->now = 0; jb->total = 0;
                char dest[512];
                if (pkg_install(jb->final_, "/dev_hdd0", dest, sizeof(dest), jb->err, sizeof(jb->err), &jb->now, &jb->total)) { unlink(jb->final_); state = J_DONE; }
                else state = J_FAILED;      /* the pkg stays in /dev_hdd0/packages */
            }
        } else if (jb->cancel) state = J_CANCELED;
        else if (worker_quit) state = J_QUEUED;          /* app closing: resume next time */
        else {
            if (r == CURLE_WRITE_ERROR) snprintf(jb->err, sizeof(jb->err), "no space");
            else if (r != CURLE_OK) snprintf(jb->err, sizeof(jb->err), "curl %d: %s", (int)r, curl_easy_strerror(r));
            else snprintf(jb->err, sizeof(jb->err), "HTTP %ld", code);
        }
    }
    if (f) fclose(f);
    if (state != J_DONE) unlink(jb->part);
    jb->state = state;
    jobs_dirty = 1;
}

static void worker_entry(void *arg) {
    while (!worker_quit) {
        Job *next = NULL;
        for (int i = 0; i < MAX_JOBS; i++) if (jobs[i].used && jobs[i].state == J_QUEUED) { next = &jobs[i]; break; }
        if (!next) { usleep(100000); continue; }
        next->cancel = 0; next->state = J_DOWNLOADING; current_job = next;
        job_run(next);
        current_job = NULL;
    }
    worker_running = 0;
    sysThreadExit(0);
}

/* history file: one job per line, tab separated */
static void field(char *dst, int n, char **p) {
    char *t = strchr(*p, '\t'); int len = t ? (int)(t - *p) : (int)strcspn(*p, "\r\n");
    snprintf(dst, n, "%.*s", len, *p); *p += len + (t ? 1 : 0);
}
static void jobs_save(void) {
    FILE *f = fopen(DL_LIST, "w"); if (!f) return;
    for (int i = 0; i < MAX_JOBS; i++) {
        Job *j = &jobs[i]; if (!j->used) continue;
        int st = (j->state == J_DOWNLOADING || j->state == J_INSTALLING) ? J_QUEUED : j->state;
        fprintf(f, "%d\t%s\t%s\t%s\t%s\t%s\n", st, j->title, j->info, j->icon, j->url, j->err);
    }
    fclose(f);
}
static void job_paths(Job *j) {
    url_filename(j->url, j->name, sizeof(j->name));
    snprintf(j->final_, sizeof(j->final_), PKG_DIR "/%s", j->name);
    snprintf(j->part, sizeof(j->part), PKG_DIR "/%s.part", j->name);
}
static void jobs_load(void) {
    char *txt = read_file(DL_LIST, NULL); if (!txt) return;
    char *p = txt; int k = 0;
    while (*p && k < MAX_JOBS) {
        Job *j = &jobs[k]; memset(j, 0, sizeof(*j));
        char st[8]; field(st, sizeof(st), &p);
        field(j->title, sizeof(j->title), &p); field(j->info, sizeof(j->info), &p); field(j->icon, sizeof(j->icon), &p);
        field(j->url, sizeof(j->url), &p); field(j->err, sizeof(j->err), &p);
        while (*p == '\r' || *p == '\n') p++;
        if (!j->url[0]) continue;
        j->state = atoi(st); j->used = 1; j->notified = 1; job_paths(j); k++;
    }
    free(txt);
}

static int jobs_active(void) { int n = 0; for (int i = 0; i < MAX_JOBS; i++) if (jobs[i].used && jobs[i].state <= J_INSTALLING) n++; return n; }

static const char *cur_icon, *cur_info;
static void start_download(const char *url, const char *title) {
    char u[1024]; snprintf(u, sizeof(u), "%s", url);
    for (char *c = u; *c && strlen(u) + 3 < sizeof(u); c++) if (*c == ' ') { memmove(c + 3, c + 1, strlen(c + 1) + 1); c[0] = '%'; c[1] = '2'; c[2] = '0'; }
    for (int i = 0; i < MAX_JOBS; i++)
        if (jobs[i].used && !strcmp(jobs[i].url, u) && jobs[i].state != J_FAILED && jobs[i].state != J_CANCELED) { show_toast(S_ALREADY); return; }
    Job *j = NULL;
    for (int i = 0; i < MAX_JOBS && !j; i++) if (jobs[i].used && !strcmp(jobs[i].url, u)) j = &jobs[i];     /* retry an old failed one */
    for (int i = 0; i < MAX_JOBS && !j; i++) if (!jobs[i].used) j = &jobs[i];
    if (!j) {   /* full: drop the oldest finished entry */
        for (int i = 0; i < MAX_JOBS && !j; i++) if (jobs[i].state >= J_DONE) { j = &jobs[i]; }
        if (!j) { show_toast(S_FAIL); return; }
    }
    memset(j, 0, sizeof(*j));
    snprintf(j->url, sizeof(j->url), "%s", u);
    snprintf(j->title, sizeof(j->title), "%s", title ? title : "");
    snprintf(j->info, sizeof(j->info), "%s", cur_info ? cur_info : "");
    snprintf(j->icon, sizeof(j->icon), "%s", cur_icon ? cur_icon : "");
    job_paths(j);
    if (!j->title[0]) snprintf(j->title, sizeof(j->title), "%s", j->name);
    j->state = J_QUEUED; j->used = 1;
    jobs_save();
    show_toast(S_ADDED);
}

static void dl_poll(void) {
    for (int i = 0; i < MAX_JOBS; i++) {
        Job *j = &jobs[i]; if (!j->used) continue;
        if ((j->state == J_DOWNLOADING) && frame_no - j->last_frame >= 60) {
            double d = j->now - j->last_now; j->speed = d > 0 ? d * 60.0 / (frame_no - j->last_frame) : j->speed * 0.5;
            j->last_now = j->now; j->last_frame = frame_no;
        }
        if (j->state <= J_INSTALLING) j->notified = 0;
        else if (!j->notified) {
            j->notified = 1;
            char t[400];
            if (j->state == J_DONE) snprintf(t, sizeof(t), "%s  %s", j->title, S_INST_T);
            else if (j->state == J_FAILED) snprintf(t, sizeof(t), "(%s)  %s  %s", j->err, j->title, S_FAIL_T);
            else t[0] = 0;
            if (t[0]) show_toast(t);
        }
    }
    if (jobs_dirty) { jobs_dirty = 0; jobs_save(); }
}

static const char *cur_title;
static void do_action(Node *n) {
    if (n->action && !strncmp(n->action, "http://127.0.0.1", 16) && !strstr(n->action, "download.ps3")) {
        webman_async(n->action + 16, S_CMD); return;     /* store commands (font, themes...) */
    }
    if (!n->link) { show_toast(S_NOLINK); return; }
    start_download(n->link, cur_title && strcmp(n->title, n->key) == 0 ? cur_title : n->title);
}

static void activate(Node *n) {
    if (n->folder) {
        if (depth + 1 >= MAX_DEPTH) return;
        Level *L = &stack[depth + 1];
        if (load_level(L, n->tfile, n->tview)) {
            cur_title = n->title; cur_icon = n->icon;
            if (L->n == 1 && !L->items[0].folder) { do_action(&L->items[0]); free_level(L); return; }
            L->title = strdup(n->title); L->pic = n->pic ? strdup(n->pic) : (stack[depth].pic ? strdup(stack[depth].pic) : NULL);
            depth++;
        } else {
            free_level(L); show_toast(S_EMPTY);
        }
        return;
    }
    cur_title = NULL; cur_icon = n->icon;
    do_action(n);
}

/* ------------------------------------------------------------------ system */
static void sys_cb(u64 status, u64 param, void *user) {
    if (status == SYSUTIL_EXIT_GAME) running = 0;
}

static u32 pad_now, pad_prev; static int hold[32];
#define K_UP 0
#define K_DOWN 1
#define K_LEFT 2
#define K_RIGHT 3
#define K_X 4
#define K_O 5
#define K_L1 6
#define K_R1 7
#define K_START 8
#define K_TRI 9
#define K_SQ 10
static void read_pad(void) {
    padInfo info; padData d; pad_prev = pad_now; pad_now = 0;
    ioPadGetInfo(&info);
    for (int i = 0; i < MAX_PADS; i++) {
        if (!info.status[i]) continue;
        if (ioPadGetData(i, &d) != 0 || d.len == 0) { pad_now = pad_prev; return; }
        if (d.BTN_UP) pad_now |= 1 << K_UP;
        if (d.BTN_DOWN) pad_now |= 1 << K_DOWN;
        if (d.BTN_LEFT) pad_now |= 1 << K_LEFT;
        if (d.BTN_RIGHT) pad_now |= 1 << K_RIGHT;
        if (d.BTN_CROSS) pad_now |= 1 << K_X;
        if (d.BTN_CIRCLE) pad_now |= 1 << K_O;
        if (d.BTN_L1) pad_now |= 1 << K_L1;
        if (d.BTN_R1) pad_now |= 1 << K_R1;
        if (d.BTN_START) pad_now |= 1 << K_START;
        if (d.BTN_TRIANGLE) pad_now |= 1 << K_TRI;
        if (d.BTN_SQUARE) pad_now |= 1 << K_SQ;
        break;
    }
}
/* pressed now, with auto-repeat for held directions */
static int pressed(int k) {
    int down = pad_now & (1 << k), was = pad_prev & (1 << k);
    if (!down) { hold[k] = 0; return 0; }
    if (!was) { hold[k] = 1; return 1; }
    hold[k]++;
    return (k <= K_RIGHT || k == K_L1 || k == K_R1) && hold[k] > 22 && (hold[k] % 5 == 0);
}

/* ------------------------------------------------------------------ drawing */
/* ------------------------------------------------------------------ UI kit (design: Arcadia menus, RTL) */
#define C_ACCENT 0x7C3AEDFF
#define C_TEXT   0xF4F2FFFF
#define C_MUTED  0xB9B4D6FF
#define C_SOFT   0xDCD8F0FF
#define C_GOLD   0xFFD166FF
#define C_OKC    0x2EA067FF
#define C_MINT   0x9EE6C0FF
#define RX       806.0f      /* right content edge */
#define LX       42.0f       /* left content edge */
#define FOOT_Y   470.0f

static Tex t_circle, t_ring;

static void gen_circle(Tex *t, float thick) {
    if (!t->mem) return;
    u32 *p = (u32 *)t->mem; float r = 31.5f;
    for (int y = 0; y < 64; y++)
        for (int x = 0; x < 64; x++) {
            float dx = x + 0.5f - 32, dy = y + 0.5f - 32, d = sqrtf(dx * dx + dy * dy);
            float a = r - d + 0.5f; a = a < 0 ? 0 : a > 1 ? 1 : a;
            if (thick > 0) { float b = d - (r - thick) + 0.5f; b = b < 0 ? 0 : b > 1 ? 1 : b; a *= b; }
            p[y * t->w + x] = ((u32)(a * 255) << 24) | 0x00FFFFFF;
        }
    t->used_w = 64; t->used_h = 64;
}

static void draw_circle(float cx, float cy, float rad, u32 color) {
    draw_tex_uv(&t_circle, cx - rad, cy - rad, rad * 2, rad * 2, 0, 0, 64.0f / t_circle.w, 64.0f / t_circle.h, color);
}
static void draw_ringc(float cx, float cy, float rad, u32 color) {
    draw_tex_uv(&t_ring, cx - rad, cy - rad, rad * 2, rad * 2, 0, 0, 64.0f / t_ring.w, 64.0f / t_ring.h, color);
}

static void draw_rrect(float x, float y, float w, float h, float r, u32 color) {
    if (w <= 0 || h <= 0) return;
    if (r * 2 > w) r = w / 2;
    if (r * 2 > h) r = h / 2;
    float u = 32.0f / t_circle.w, v = 32.0f / t_circle.h, u2 = 64.0f / t_circle.w, v2 = 64.0f / t_circle.h;
    draw_tex_uv(&t_circle, x, y, r, r, 0, 0, u, v, color);
    draw_tex_uv(&t_circle, x + w - r, y, r, r, u, 0, u2, v, color);
    draw_tex_uv(&t_circle, x, y + h - r, r, r, 0, v, u, v2, color);
    draw_tex_uv(&t_circle, x + w - r, y + h - r, r, r, u, v, u2, v2, color);
    draw_quad(x + r, y, w - 2 * r, h, color);
    draw_quad(x, y + r, r, h - 2 * r, color);
    draw_quad(x + w - r, y + r, r, h - 2 * r, color);
}

static void draw_line(float x1, float y1, float x2, float y2, float th, u32 color) {
    float dx = x2 - x1, dy = y2 - y1, l = sqrtf(dx * dx + dy * dy);
    if (l <= 0) return;
    float nx = -dy / l * th / 2, ny = dx / l * th / 2;
    tiny3d_SetPolygon(TINY3D_QUADS);
    tiny3d_VertexPos(x1 + nx, y1 + ny, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x2 + nx, y2 + ny, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x2 - nx, y2 - ny, 65535); tiny3d_VertexColor(color);
    tiny3d_VertexPos(x1 - nx, y1 - ny, 65535); tiny3d_VertexColor(color);
    tiny3d_End();
}

static void glyph_x(float cx, float cy, float r, int on_accent) {
    if (on_accent) draw_circle(cx, cy, r, 0xFFFFFF2E);
    else { draw_circle(cx, cy, r, 0x1E2240FF); draw_ringc(cx, cy, r, 0x7DA2FFFF); }
    float k = r * 0.36f; u32 c = on_accent ? 0xFFFFFFFF : 0x7DA2FFFF;
    draw_line(cx - k, cy - k, cx + k, cy + k, r * 0.16f, c);
    draw_line(cx + k, cy - k, cx - k, cy + k, r * 0.16f, c);
}
static void glyph_o(float cx, float cy, float r) {
    draw_circle(cx, cy, r, 0x1E2240FF); draw_ringc(cx, cy, r, 0xFF7A8AFF);
    draw_ringc(cx, cy, r * 0.5f, 0xFF7A8AFF);
}
static void glyph_tri(float cx, float cy, float r) {
    draw_circle(cx, cy, r, 0x1E2240FF); draw_ringc(cx, cy, r, 0x5EE0B0FF);
    float k = r * 0.5f, t = r * 0.13f;
    draw_line(cx, cy - k, cx + k * 0.95f, cy + k * 0.6f, t, 0x5EE0B0FF);
    draw_line(cx + k * 0.95f, cy + k * 0.6f, cx - k * 0.95f, cy + k * 0.6f, t, 0x5EE0B0FF);
    draw_line(cx - k * 0.95f, cy + k * 0.6f, cx, cy - k, t, 0x5EE0B0FF);
}
static void glyph_sq(float cx, float cy, float r) {
    draw_circle(cx, cy, r, 0x1E2240FF); draw_ringc(cx, cy, r, 0xE58AE6FF);
    float k = r * 0.38f, t = r * 0.13f;
    draw_line(cx - k, cy - k, cx + k, cy - k, t, 0xE58AE6FF); draw_line(cx + k, cy - k, cx + k, cy + k, t, 0xE58AE6FF);
    draw_line(cx + k, cy + k, cx - k, cy + k, t, 0xE58AE6FF); draw_line(cx - k, cy + k, cx - k, cy - k, t, 0xE58AE6FF);
}
static void glyph_check(float cx, float cy, float s, u32 c) {
    draw_line(cx - s * 0.45f, cy, cx - s * 0.12f, cy + s * 0.33f, s * 0.16f, c);
    draw_line(cx - s * 0.12f, cy + s * 0.33f, cx + s * 0.5f, cy - s * 0.35f, s * 0.16f, c);
}

/* text helpers: everything in the store is stored visually, so "right aligned" just means x = right - width */
static int has_arabic(const char *s) {
    for (const unsigned char *p = (const unsigned char *)s; p && *p; p++)
        if (*p == 0xEF && (p[1] == 0xBA || p[1] == 0xBB || p[1] == 0xAD || p[1] == 0xAE)) return 1;
        else if (*p >= 0xD8 && *p <= 0xDB) return 1;
    return 0;
}

static float glyph_adv(u32 cp, float size) { Glyph *g = glyph(cp); return g ? g->adv * size / GLYPH_PX : 0; }

/* draw `s` so it ends at `right`; if longer than max_w it is cut with "…" on the side where reading ends */
static void text_r(const char *s, float right, float y, float size, u32 color, float max_w) {
    if (!s || !*s) return;
    float w = text_width(s, size);
    if (max_w <= 0 || w <= max_w) { draw_text(s, right - w, y, size, color, 0); return; }
    float ell = glyph_adv(0x2026, size);
    if (has_arabic(s)) {
        /* RTL: reading ends at the left, drop glyphs from the left */
        const char *p = s; float cut = 0;
        while (*p && w - cut + ell > max_w) { const char *q = p; cut += glyph_adv(utf8_next(&q), size); p = q; }
        draw_text(p, right - (w - cut), y, size, color, 0);
        draw_text("\xE2\x80\xA6", right - (w - cut) - ell, y, size, color, 0);
    } else {
        draw_text(s, right - max_w, y, size, color, max_w - ell);
        /* find drawn width */
        const char *p = s; float acc = 0;
        while (*p) { const char *q = p; float a = glyph_adv(utf8_next(&q), size); if (acc + a > max_w - ell) break; acc += a; p = q; }
        draw_text("\xE2\x80\xA6", right - max_w + acc, y, size, color, 0);
    }
}
static void text_rb(const char *s, float right, float y, float size, u32 color, float max_w) {
    text_r(s, right, y, size, color, max_w); text_r(s, right - 0.5f, y, size, color, max_w);
}
static void text_c(const char *s, float cx, float y, float size, u32 color, float max_w) {
    float w = text_width(s, size); if (max_w > 0 && w > max_w) w = max_w;
    text_r(s, cx + w / 2, y, size, color, max_w);
}

/* ------------------------------------------------------------------ store helpers for the views */
static int is_game(Node *n) { return (n->folder && strstr(n->tview, "_link")) || (!n->folder && n->link); }
static int level_is_games(Level *L) { int g = 0; for (int i = 0; i < L->n; i++) g += is_game(&L->items[i]); return L->n && g * 2 >= L->n; }

static const char *region_of(Level *L) {
    char f[256]; int i = 0;
    for (; L->file[i] && i < 255; i++) f[i] = (L->file[i] >= 'A' && L->file[i] <= 'Z') ? L->file[i] + 32 : L->file[i];
    f[i] = 0;
    const char *b = strrchr(f, '/'); b = b ? b + 1 : f;
    if (strstr(b, "usa")) return S_USA;
    if (strstr(b, "europ") || strstr(b, "eur")) return S_EUR;
    if (strstr(b, "jap")) return S_JPN;
    return NULL;
}

/* "NPUB30024 - 130 MB" -> id, size */
static int split_info(const char *info, char *id, int idn, char *size, int sn) {
    id[0] = size[0] = 0;
    if (!info) return 0;
    const char *d = strstr(info, " - ");
    if (!d || has_arabic(info)) return 0;
    snprintf(id, idn, "%.*s", (int)(d - info), info);
    snprintf(size, sn, "%s", d + 3);
    return 1;
}

/* ------------------------------------------------------------------ shared chrome */
static void draw_backdrop(const char *pic, int strong) {
    Tex *bg = pic ? get_image(bgs, BG_SLOTS, pic) : NULL;
    if (bg) draw_image_fill(bg, 0, 0, SW, SH, 0xFFFFFFFF);
    else draw_grad(0, 0, SW, SH, 0x0B1A4AFF, 0x3A1060FF);
    if (strong) draw_quad(0, 0, SW, SH, 0x070914DC);
    else draw_grad(0, 0, SW, SH, 0x0709148C, 0x070914F2);
}

typedef struct { int kind; const char *label; } Hint;   /* kind: 0 X, 1 O, 2 L1/R1 */
static void draw_footer(const Hint *h, int n, const char *left) {
    draw_quad(0, FOOT_Y, SW, SH - FOOT_Y, 0x04050CD9);
    draw_quad(0, FOOT_Y, SW, 1, 0xFFFFFF14);
    float x = RX, cy = FOOT_Y + 21;
    for (int i = 0; i < n; i++) {
        if (h[i].kind == 0) { glyph_x(x - 8.5f, cy, 8.5f, 0); x -= 22; }
        else if (h[i].kind == 1) { glyph_o(x - 8.5f, cy, 8.5f); x -= 22; }
        else if (h[i].kind == 3) { glyph_tri(x - 8.5f, cy, 8.5f); x -= 22; }
        else if (h[i].kind == 4) { glyph_sq(x - 8.5f, cy, 8.5f); x -= 22; }
        else {
            float bw = text_width("L1 / R1", 8.6f) + 12;
            draw_rrect(x - bw, cy - 8, bw, 16, 4, 0x1E2240FF);
            draw_text("L1 / R1", x - bw + 6, cy + 3.2f, 8.6f, C_TEXT, 0);
            x -= bw + 6;
        }
        float w = text_width(h[i].label, 10);
        draw_text(h[i].label, x - w, cy + 3.6f, 10, C_SOFT, 0);
        x -= w + 22;
    }
    if (left) draw_text(left, LX, cy + 3.4f, 8.6f, 0x8F89B3FF, 0);
}

static void draw_toast(void) {
    if (toast_frames <= 0) return;
    toast_frames--;
    float w = text_width(toast, 10.5f) + 32; if (w > SW - 80) w = SW - 80;
    float x = (SW - w) / 2, y = FOOT_Y - 40;
    draw_rrect(x, y, w, 28, 14, 0x1A2150F2);
    text_r(toast, x + w - 16, y + 18, 10.5f, C_TEXT, w - 32);
}

/* breadcrumb for level d: deepest first in visual order so it reads right-to-left */
static void crumbs(int d, char *out, int n) {
    out[0] = 0;
    for (int i = d; i >= 0; i--) {
        const char *t = i == 0 ? S_HOME : stack[i].title;
        if (!t) continue;
        if (out[0]) strncat(out, "  \xE2\x80\xB9  ", n - strlen(out) - 1);
        strncat(out, t, n - strlen(out) - 1);
    }
}

/* left side of the header: downloads button with live progress */
static void draw_dl_pill(const char *count) {
    int act = jobs_active();
    Job *j = current_job;
    char t[160];
    if (j && j->state == J_INSTALLING) snprintf(t, sizeof(t), "%d%%  %s", j->total > 0 ? (int)(j->now * 100 / j->total) : 0, S_INSTALLING);
    else if (j) snprintf(t, sizeof(t), "%d%%  %s", j->total > 0 ? (int)(j->now * 100 / j->total) : 0, S_DOWNLOADS);
    else snprintf(t, sizeof(t), "%s", S_DOWNLOADS);
    float w = text_width(t, 10) + 46, x = LX, y = 30;
    draw_rrect(x, y, w, 28, 14, act ? 0x7C3AED33 : 0xFFFFFF0F);
    glyph_tri(x + w - 17, y + 14, 8);
    draw_text(t, x + 12, y + 18, 10, C_TEXT, 0);
    if (j && j->total > 0) {
        float pct = (float)(j->now / j->total); if (pct > 1) pct = 1;
        draw_rrect(x + 10, y + 24, (w - 20), 2.5f, 1.2f, 0xFFFFFF22);
        draw_rrect(x + 10 + (w - 20) * (1 - pct), y + 24, (w - 20) * pct, 2.5f, 1.2f, C_ACCENT);
    }
    if (act > 1) {
        char b[8]; snprintf(b, sizeof(b), "%d", act);
        draw_circle(x + w, y + 3, 8, 0xFF5470FF);
        draw_text(b, x + w - text_width(b, 8.5f) / 2, y + 6, 8.5f, 0xFFFFFFFF, 0);
    }
    if (count) draw_text(count, x + w + 14, y + 18, 10, C_MUTED, 0);
}

static void draw_header(Level *L, const char *count) {
    if (depth == 0) {
        draw_rrect(RX - 34, 26, 34, 34, 9, C_ACCENT);
        draw_text("A", RX - 17 - text_width("A", 18) / 2, 50, 18, 0xFFFFFFFF, 0);
        text_rb(S_APPNAME, RX - 46, 44, 18.5f, C_TEXT, 0);
        text_r("A R C A D I A", RX - 46, 57, 8, C_MUTED, 0);
    } else {
        char c[600]; crumbs(depth - 1, c, sizeof(c));
        Node *icon_src = NULL; (void)icon_src;
        text_r(c, RX, 38, 9.3f, C_MUTED, 600);
        text_rb(L->title, RX, 60, 18.5f, C_TEXT, 600);
    }
    draw_dl_pill(count);
}

/* ------------------------------------------------------------------ home (top level): category tiles + preview */
static Level pv; static int pv_for = -1, pv_rest, home_row, pv_sel, pv_first;
static void preview_update(Level *L) {
    if (pv_for == L->sel) return;
    if (++pv_rest < 12) return;
    if (pv_for >= 0) free_level(&pv);
    memset(&pv, 0, sizeof(pv)); pv_for = L->sel;
    Node *n = &L->items[L->sel];
    if (n->folder && !strstr(n->tview, "_link")) { if (!load_level(&pv, n->tfile, n->tview)) free_level(&pv); }
}

static int home_first;
static void draw_home(Level *L) {
    draw_backdrop(L->pic, 0);
    char cnt[64]; snprintf(cnt, sizeof(cnt), "%s %d", S_ITEMS, L->n);
    draw_header(L, NULL);
    text_rb(S_SECTIONS, RX, 98, 14.5f, C_TEXT, 0);

    const float gap = 12, tw = 93, fw = 109;
    int vis = (int)((RX - LX - fw) / (tw + gap)) + 1;
    if (L->sel < home_first) home_first = L->sel;
    if (L->sel >= home_first + vis) home_first = L->sel - vis + 1;
    float x = RX, base = 248;
    for (int i = home_first; i < L->n && i < home_first + vis; i++) {
        Node *n = &L->items[i]; int f = i == L->sel;
        float w = f ? fw : tw, h = f ? 134 : 118, y = base - h, ic = f ? 80 : 66;
        if (f && home_row == 0) { draw_rrect(x - w - 4, y - 4, w + 8, h + 8, 17, 0x7C3AED40); draw_rrect(x - w - 1.5f, y - 1.5f, w + 3, h + 3, 14, C_ACCENT); draw_rrect(x - w, y, w, h, 13, 0x2A1E5CFF); }
        else if (f) { draw_rrect(x - w - 1.5f, y - 1.5f, w + 3, h + 3, 14, 0x7C3AED80); draw_rrect(x - w, y, w, h, 13, 0x221A48FF); }
        else draw_rrect(x - w, y, w, h, 13, 0xFFFFFF10);
        Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        if (t) draw_image_fit(t, x - w / 2 - ic / 2, y + 9, ic, ic, 0xFFFFFFFF);
        else draw_rrect(x - w / 2 - ic / 2, y + 9, ic, ic, 10, 0xFFFFFF22);
        text_c(n->title, x - w / 2, y + h - 12, f ? 10.5f : 9.6f, f ? 0xFFFFFFFF : C_SOFT, w - 10);
        x -= w + gap;
    }

    /* what is inside the focused section */
    preview_update(L);
    Node *cur = &L->items[L->sel];
    text_rb(S_INSIDE, RX, 288, 14.5f, C_TEXT, 0);
    text_r(cur->title, LX + 300, 288, 10, C_MUTED, 300);
    if (pv_for == L->sel && pv.n) {
        float cx = RX, cw = 143;
        if (pv_sel < pv_first) pv_first = pv_sel;
        if (pv_sel >= pv_first + 5) pv_first = pv_sel - 4;
        for (int i = pv_first; i < pv.n && i < pv_first + 5; i++) {
            Node *n = &pv.items[i];
            if (home_row == 1 && i == pv_sel) { draw_rrect(cx - cw - 4, 298, cw + 8, 87, 11, 0x7C3AED40); draw_rrect(cx - cw - 2, 300, cw + 4, 83, 10, C_ACCENT); }
            draw_rrect(cx - cw, 302, cw, 79, 8, home_row == 1 && i == pv_sel ? 0x2A1E5CFF : 0x0000004D);
            Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
            if (t) draw_image_fit(t, cx - cw, 302, cw, 79, 0xFFFFFFFF);
            text_rb(n->title, cx, 400, 10, C_TEXT, cw);
            if (n->info) text_r(n->info, cx, 414, 8.6f, C_MUTED, cw);
            cx -= cw + 12;
        }
    } else if (pv_for != L->sel) text_r(S_LOADING, RX, 340, 10, C_MUTED, 0);
    else text_r(cur->info ? cur->info : S_EMPTY, RX, 340, 10, C_MUTED, 600);

    Hint h[] = { {0, S_OPENL}, {1, home_row ? S_BACK : S_EXITL}, {3, S_DOWNLOADS}, {2, S_NAV} };
    char pos[32];
    if (home_row == 1 && pv_for == L->sel) snprintf(pos, sizeof(pos), "%d / %d", pv_sel + 1, pv.n);
    else snprintf(pos, sizeof(pos), "%d / %d", L->sel + 1, L->n);
    draw_footer(h, 4, pos);
}

/* ------------------------------------------------------------------ grid view (sections and games) */
#define GCOLS 4
#define GROWS 3
#define GCELL_W 124.0f
#define GCELL_H 112.0f
#define GGAP 10.0f
#define GTOP 104.0f
#define PANEL_W 220.0f

static void draw_grid(Level *L) {
    int games = level_is_games(L);
    draw_backdrop(L->pic, 1);
    char cnt[64]; snprintf(cnt, sizeof(cnt), "%s %d", S_ITEMS, L->n);
    draw_header(L, cnt);
    if (!L->n) { text_r(S_EMPTY, RX, 250, 13, C_TEXT, 0); }

    int row = L->sel / GCOLS;
    if (row < L->top) L->top = row;
    if (row >= L->top + GROWS) L->top = row - GROWS + 1;
    for (int i = L->top * GCOLS; i < L->n && i < (L->top + GROWS) * GCOLS; i++) {
        Node *n = &L->items[i]; int f = i == L->sel;
        int c = i % GCOLS, r = i / GCOLS - L->top;
        float x = RX - (c + 1) * GCELL_W - c * GGAP, y = GTOP + r * (GCELL_H + GGAP);
        if (f) { draw_rrect(x - 2, y - 2, GCELL_W + 4, GCELL_H + 4, 12, C_ACCENT); draw_rrect(x, y, GCELL_W, GCELL_H, 10, 0x2A1E5CFF); }
        else draw_rrect(x, y, GCELL_W, GCELL_H, 10, 0xFFFFFF0D);
        Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        float ih = GCELL_H - 30;
        if (t) draw_image_fit(t, x + 5, y + 5, GCELL_W - 10, ih, 0xFFFFFFFF);
        else draw_rrect(x + 5, y + 5, GCELL_W - 10, ih, 8, 0xFFFFFF14);
        text_r(n->title, x + GCELL_W - 6, y + GCELL_H - 9, 9.4f, f ? 0xFFFFFFFF : C_SOFT, GCELL_W - 12);
    }
    /* scroll bar */
    int rows = (L->n + GCOLS - 1) / GCOLS;
    if (rows > GROWS) {
        float th = GROWS * (GCELL_H + GGAP) - GGAP, bh = th * GROWS / rows, by = GTOP + (th - bh) * L->top / (rows - GROWS);
        float sx = RX - GCOLS * (GCELL_W + GGAP) - 2;
        draw_rrect(sx, GTOP, 3, th, 1.5f, 0xFFFFFF1F); draw_rrect(sx, by, 3, bh, 1.5f, 0xFFFFFFAA);
    }

    /* detail panel */
    if (L->n) {
        Node *n = &L->items[L->sel];
        float px = LX, py = GTOP - 4, pw = PANEL_W, ph = FOOT_Y - 18 - py;
        draw_rrect(px, py, pw, ph, 14, 0x101228E6);
        draw_rrect(px + 12, py + 12, pw - 24, 140, 10, 0x00000059);
        Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        if (t) draw_image_fit(t, px + 12, py + 12, pw - 24, 140, 0xFFFFFFFF);
        text_rb(n->title, px + pw - 14, py + 176, 13.5f, C_TEXT, pw - 28);
        char id[64], size[64];
        if (is_game(n) && split_info(n->info, id, sizeof(id), size, sizeof(size))) {
            float cx = px + pw - 14, cy = py + 190;
            const char *chips[3] = { id, size, region_of(L) };
            for (int k = 0; k < 3; k++) {
                if (!chips[k]) continue;
                float w = text_width(chips[k], 8.8f) + 14;
                if (cx - w < px + 12) { cx = px + pw - 14; cy += 26; }
                draw_rrect(cx - w, cy, w, 20, 6, 0xFFFFFF14);
                draw_text(chips[k], cx - w + 7, cy + 13.6f, 8.8f, C_TEXT, 0);
                cx -= w + 6;
            }
        } else if (n->info) text_r(n->info, px + pw - 14, py + 200, 9.6f, C_MUTED, pw - 28);
        /* action button */
        float bw = pw - 28, bx = px + 14, by = py + ph - 48;
        draw_rrect(bx, by, bw, 34, 10, C_ACCENT);
        const char *lbl = is_game(n) ? S_DLINST : n->folder ? S_OPENL : S_DLINST;
        float lw = text_width(lbl, 11.5f), gx = bx + bw / 2 + (lw + 22) / 2;
        glyph_x(gx - 8, by + 17, 8, 1);
        draw_text(lbl, gx - 22 - lw, by + 21.5f, 11.5f, 0xFFFFFFFF, 0);
    }
    Hint h[] = { {0, games ? S_DLINST : S_OPENL}, {1, S_BACK}, {3, S_DOWNLOADS}, {2, S_PAGE} };
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", L->n ? L->sel + 1 : 0, L->n);
    draw_footer(h, 4, pos);
}

/* ------------------------------------------------------------------ game videos (.avp: JPEG frames, decoded with the PS3's jpgdec) */
#define VID_DIR APPDIR "USRDIR/videos"
#define VID_SLOTS 3
static char vid_base[512];                 /* from the store: USRDIR/PREVIEWS.txt */
static Tex vid_tex[VID_SLOTS];
static int vid_slot = -1;
static char vid_key[48];                    /* key of what is loaded / being shown */
static u8 *vid_data; static u32 vid_size, vid_frames, vid_fps, vid_w, vid_h, vid_frame;
static u32 vid_next_at;
static int vid_wait;                         /* frames the game page has been open on this key */
/* fetch thread */
static volatile int vf_busy, vf_quit, vf_running;
static char vf_key[48];
static char vf_missing[64][48]; static int vf_nmiss;
static sys_ppu_thread_t vf_thread;

static u32 crc32_str(const char *s) {
    u32 c = 0xFFFFFFFF;
    for (; *s; s++) { c ^= (u8)*s; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320 & -(c & 1)); }
    return ~c;
}
/* same rule as preview_key() in Arcadia Studio */
static void key_of(const char *title, const char *info, char *out, int n) {
    if (info) for (const char *p = info; *p; p++) {
        int ok = 1;
        for (int i = 0; i < 9 && ok; i++) ok = i < 4 ? (p[i] >= 'A' && p[i] <= 'Z') : (p[i] >= '0' && p[i] <= '9');
        if (ok && (p == info || !((p[-1] >= 'A' && p[-1] <= 'Z') || (p[-1] >= '0' && p[-1] <= '9') || (p[-1] >= 'a' && p[-1] <= 'z')))
            && !((p[9] >= 'A' && p[9] <= 'Z') || (p[9] >= 'a' && p[9] <= 'z') || (p[9] >= '0' && p[9] <= '9'))) {
            snprintf(out, n, "%.9s", p); return;
        }
    }
    int j = 0;
    for (const char *p = title ? title : ""; *p && j < 48 && j < n - 1; p++) {
        char ch = *p;
        if (ch >= 'A' && ch <= 'Z') ch += 32;
        if ((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')) out[j++] = ch;
    }
    out[j] = 0;
    if (!j) snprintf(out, n, "%08x", crc32_str(title ? title : ""));
}

static void vid_path(const char *key, char *out, int n) { snprintf(out, n, VID_DIR "/%s.avp", key); }
static int file_exists(const char *p) { struct stat st; return stat(p, &st) == 0 && st.st_size > 16; }
static int vf_is_missing(const char *key) { for (int i = 0; i < vf_nmiss; i++) if (!strcmp(vf_missing[i], key)) return 1; return 0; }

/* one HTTP GET into a file (or memory when mem != NULL), with the DNS fallback; returns 1 on HTTP success */
typedef struct { char *buf; size_t len, cap; } MemBuf;
static size_t mem_write(void *p, size_t sz, size_t n, void *u) {
    MemBuf *m = (MemBuf *)u; size_t add = sz * n;
    if (m->len + add + 1 > m->cap) return 0;
    memcpy(m->buf + m->len, p, add); m->len += add; m->buf[m->len] = 0; return add;
}
static char http_err[160]; static volatile int http_fails;
static int http_get(const char *url, const char *path, MemBuf *mem) {
    FILE *f = NULL;
    if (!mem) { f = fopen(path, "wb"); if (!f) return 0; }
    CURL *c = curl_easy_init(); if (!c) { if (f) fclose(f); return 0; }
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_MAXREDIRS, 10L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "ArcadiaPS3/1.0");
    if (mem) { curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, mem_write); curl_easy_setopt(c, CURLOPT_WRITEDATA, mem); }
    else { curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, dl_write); curl_easy_setopt(c, CURLOPT_WRITEDATA, f); }
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 15L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 90L);
    curl_easy_setopt(c, CURLOPT_FAILONERROR, 1L);
    CURLcode r = curl_easy_perform(c);
    struct curl_slist *resolve = NULL;
    for (int tries = 0; r == CURLE_COULDNT_RESOLVE_HOST && tries < 4 && !vf_quit; tries++) {
        char *eff = NULL, host[256], ip[32], entry[320];
        curl_easy_getinfo(c, CURLINFO_EFFECTIVE_URL, &eff);
        url_host(eff ? eff : url, host, sizeof(host));
        if (!host[0] || !dns_fallback(host, ip, sizeof(ip))) break;
        snprintf(entry, sizeof(entry), "%s:80:%s", host, ip); resolve = curl_slist_append(resolve, entry);
        snprintf(entry, sizeof(entry), "%s:443:%s", host, ip); resolve = curl_slist_append(resolve, entry);
        curl_easy_setopt(c, CURLOPT_RESOLVE, resolve);
        if (mem) mem->len = 0;
        r = curl_easy_perform(c);
    }
    if (r != CURLE_OK) {
        long code = 0; curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        char host[128]; url_host(url, host, sizeof(host));
        if (r == CURLE_HTTP_RETURNED_ERROR) snprintf(http_err, sizeof(http_err), "%s HTTP %ld", host, code);
        else snprintf(http_err, sizeof(http_err), "%s curl %d: %s", host, (int)r, curl_easy_strerror(r));
        http_fails++;
    }
    curl_easy_cleanup(c);
    if (resolve) curl_slist_free_all(resolve);
    if (f) fclose(f);
    return r == CURLE_OK;
}

/* RAWG.io: screenshots of the game (shown as a slideshow when the game has no video) */
#ifndef RAWG_KEY
#define RAWG_KEY ""
#endif
static char rawg_key[64];
static char vf_name[200];
#define SHOT_DIR APPDIR "USRDIR/shots"
#define MAX_SHOTS 6

static void url_encode(const char *s, char *out, int n) {
    int j = 0;
    for (; *s && j < n - 4; s++) {
        unsigned char ch = *s;
        if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.') out[j++] = ch;
        else { snprintf(out + j, 4, "%%%02X", ch); j += 3; }
    }
    out[j] = 0;
}

static int rawg_fetch(const char *key, const char *name) {
    static char json[256 * 1024];
    char q[400], url[700];
    url_encode(name, q, sizeof(q));
    snprintf(url, sizeof(url), "https://api.rawg.io/api/games?key=%s&search=%s&page_size=1&search_precise=true", rawg_key, q);
    MemBuf m = { json, 0, sizeof(json) }; json[0] = 0;
    if (!http_get(url, NULL, &m)) return 0;
    char *p = strstr(json, "\"short_screenshots\"");
    if (!p) return 0;
    char *end = strchr(p, ']');
    mkdir(APPDIR "USRDIR", 0777); mkdir(SHOT_DIR, 0777);
    int got = 0;
    while (got < MAX_SHOTS && (p = strstr(p, "\"image\":\"")) && (!end || p < end)) {
        p += 9;
        char img[512]; int j = 0;
        while (*p && *p != '"' && j < (int)sizeof(img) - 1) { if (*p == '\\' && p[1] == '/') p++; img[j++] = *p++; }
        img[j] = 0;
        /* smaller copy from RAWG's resizer */
        char small[600]; const char *mk = strstr(img, "/media/");
        if (mk && !strstr(img, "/resize/")) snprintf(small, sizeof(small), "%.*s/media/resize/640/-/%s", (int)(mk - img), img, mk + 7);
        else snprintf(small, sizeof(small), "%s", img);
        char path[300], part[310];
        snprintf(path, sizeof(path), SHOT_DIR "/%s_%d.jpg", key, got); snprintf(part, sizeof(part), "%s.part", path);
        if (http_get(small, part, NULL) || http_get(img, part, NULL)) { unlink(path); if (rename(part, path) == 0) got++; }
        else unlink(part);
    }
    if (got) {
        char cnt[300]; snprintf(cnt, sizeof(cnt), SHOT_DIR "/%s.shots", key);
        FILE *f = fopen(cnt, "w"); if (f) { fprintf(f, "%d\n", got); fclose(f); }
    }
    return got > 0;
}

static void vf_entry(void *arg) {
    while (!vf_quit) {
        if (!vf_busy) { usleep(50000); continue; }
        char key[48], name[200], url[700], part[300], dest[300];
        snprintf(key, sizeof(key), "%s", vf_key); snprintf(name, sizeof(name), "%s", vf_name);
        int ok = 0;
        {                            /* 1) a real video: the store's own host, else the shared GitHub releases */
            if (vid_base[0]) snprintf(url, sizeof(url), "%s%s.avp", vid_base, key);
            else snprintf(url, sizeof(url), "https://github.com/Cruyfford/Arcadia-Studio/releases/download/vid%u/%s.avp", crc32_str(key) % 8, key);
            vid_path(key, dest, sizeof(dest)); snprintf(part, sizeof(part), "%s.part", dest);
            mkdir(APPDIR "USRDIR", 0777); mkdir(VID_DIR, 0777);
            if (http_get(url, part, NULL)) {
                FILE *g = fopen(part, "rb"); char mg[4] = {0};
                if (g) { if (fread(mg, 1, 4, g) != 4) mg[0] = 0; fclose(g); }
                if (!memcmp(mg, "AVP1", 4)) { unlink(dest); ok = rename(part, dest) == 0; }
            }
            if (!ok) unlink(part);
        }
        if (!ok && rawg_key[0] && name[0]) ok = rawg_fetch(key, name);   /* 2) screenshots from RAWG */
        if (!ok && vf_nmiss < 64) snprintf(vf_missing[vf_nmiss++], 48, "%s", key);
        vf_busy = 0;
    }
    vf_running = 0;
    sysThreadExit(0);
}

static int shots_count(const char *key) {
    char p[300]; snprintf(p, sizeof(p), SHOT_DIR "/%s.shots", key);
    char *b = read_file(p, NULL); if (!b) return 0;
    int n = atoi(b); free(b); return n > MAX_SHOTS ? MAX_SHOTS : n;
}

static void vid_unload(void) {
    free(vid_data); vid_data = NULL; vid_size = vid_frames = 0; vid_slot = -1; vid_key[0] = 0;
}

static u32 be32p(const u8 *p) { return ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | p[3]; }

static int vid_load(const char *key) {
    char p[300]; vid_path(key, p, sizeof(p));
    long n = 0; char *d = read_file(p, &n);
    if (!d || n < 20 || memcmp(d, "AVP1", 4)) { free(d); return 0; }
    const u8 *h = (const u8 *)d;
    u32 w = (h[4] << 8) | h[5], hh = (h[6] << 8) | h[7], fps = (h[8] << 8) | h[9], fr = be32p(h + 12);
    if (!fr || w > 640 || hh > 360 || 16 + 4 * (fr + 1) > (u32)n || !fps || fps > 60) { free(d); return 0; }
    vid_unload();
    vid_data = (u8 *)d; vid_size = n; vid_w = w; vid_h = hh; vid_fps = fps; vid_frames = fr; vid_frame = 0;
    snprintf(vid_key, sizeof(vid_key), "%s", key);
    vid_next_at = frame_no;
    return 1;
}

/* decode the next frame into the next texture slot (several slots: a slot is never rewritten while it may be on screen) */
static void vid_step(void) {
    if (!vid_data || frame_no < vid_next_at) return;
    vid_next_at = frame_no + (60 + vid_fps / 2) / vid_fps;
    const u8 *offs = vid_data + 16;
    u32 a = be32p(offs + 4 * vid_frame), b = be32p(offs + 4 * (vid_frame + 1));
    vid_frame = (vid_frame + 1) % vid_frames;
    if (b <= a || b > vid_size) return;
    jpgData jd; memset(&jd, 0, sizeof(jd));
    if (jpgLoadFromBuffer(vid_data + a, b - a, &jd) != 0 || !jd.bmp_out) return;
    int s = (vid_slot + 1) % VID_SLOTS;
    Tex *t = &vid_tex[s];
    if (t->mem) {
        int w = jd.width < (u32)t->w ? (int)jd.width : t->w, h = jd.height < (u32)t->h ? (int)jd.height : t->h;
        for (int y = 0; y < h; y++) memcpy(t->mem + y * t->w * 4, (u8 *)jd.bmp_out + y * jd.pitch, w * 4);
        t->used_w = w; t->used_h = h; vid_slot = s;
    }
    free(jd.bmp_out);
}

/* called every frame while a game page is open */
static Tex *video_for(Node *n, const char *search) {
    char key[48]; key_of(n->title, n->info, key, sizeof(key));
    if (strcmp(key, vid_key)) {
        if (vid_data) vid_unload();
        char p[300]; vid_path(key, p, sizeof(p));
        static char waiting[48];
        if (strcmp(waiting, key)) { snprintf(waiting, sizeof(waiting), "%s", key); vid_wait = 0; }
        vid_wait++;
        if (file_exists(p)) { if (!vf_busy || strcmp(vf_key, key)) vid_load(key); }
        else if (vid_wait > 20 && vf_running && !vf_busy && !vf_is_missing(key) && !shots_count(key)) {
            snprintf(vf_key, sizeof(vf_key), "%s", key); snprintf(vf_name, sizeof(vf_name), "%s", search ? search : ""); vf_busy = 1;
        }
        if (strcmp(key, vid_key)) return NULL;
    }
    vid_step();
    return vid_slot >= 0 ? &vid_tex[vid_slot] : NULL;
}

static void video_init(void) {
    for (int i = 0; i < VID_SLOTS; i++) { tex_make(&vid_tex[i], 320, 180); if (vid_tex[i].mem) memset(vid_tex[i].mem, 0, 320 * 180 * 4); }
    char *b = read_file(STORE "USRDIR/PREVIEWS.txt", NULL);
    if (b) {
        int l = (int)strcspn(b, "\r\n \t");
        if (l > 8 && l < (int)sizeof(vid_base) - 2 && !strncmp(b, "http", 4)) {
            snprintf(vid_base, sizeof(vid_base), "%.*s", l, b);
            if (vid_base[l - 1] != '/') strcat(vid_base, "/");
        }
        free(b);
    }
    snprintf(rawg_key, sizeof(rawg_key), "%s", RAWG_KEY);
    const char *kf[2] = { STORE "USRDIR/RAWG.txt", APPDIR "USRDIR/RAWG.txt" };
    for (int i = 0; i < 2 && !rawg_key[0]; i++) {
        char *k = read_file(kf[i], NULL);
        if (k) { int l = (int)strcspn(k, "\r\n \t"); if (l > 10 && l < 60) snprintf(rawg_key, sizeof(rawg_key), "%.*s", l, k); free(k); }
    }
#ifndef HOST_PREVIEW
    vf_running = 1;
    if (sysThreadCreate(&vf_thread, vf_entry, NULL, 1300, 0x20000, 0, "arcadia_vid") != 0) vf_running = 0;
#endif
}

/* ------------------------------------------------------------------ trailer player: MP4 (H.264 via the PS3 hardware decoder, AAC via faad2) */
#define TR_DIR APPDIR "USRDIR/trailers"
enum { TR_IDLE, TR_SEARCH, TR_DOWNLOAD, TR_READY, TR_NONE, TR_FAIL };
static volatile int tr_state, tr_cancel;
static volatile double tr_now, tr_total;
static char tr_key[48], tr_name[200], tr_title[256], tr_path[300], tr_err[160];
static sys_ppu_thread_t tr_thread;

static int tr_progress(void *u, curl_off_t total, curl_off_t now, curl_off_t ut, curl_off_t un) {
    tr_total = (double)total; tr_now = (double)now; return tr_cancel ? 1 : 0;
}
static int http_get_progress(const char *url, const char *path) {
    FILE *f = fopen(path, "wb"); if (!f) return 0;
    CURL *c = curl_easy_init(); if (!c) { fclose(f); return 0; }
    static char fbuf[1 << 20]; setvbuf(f, fbuf, _IOFBF, sizeof(fbuf));
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "Mozilla/5.0 (PLAYSTATION 3; 4.92)");
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, dl_write);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, f);
    curl_easy_setopt(c, CURLOPT_NOPROGRESS, 0L);
    curl_easy_setopt(c, CURLOPT_XFERINFOFUNCTION, tr_progress);
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 20L);
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_LIMIT, 1L);
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_TIME, 40L);
    curl_easy_setopt(c, CURLOPT_FAILONERROR, 1L);
    curl_easy_setopt(c, CURLOPT_BUFFERSIZE, 512L * 1024);
    CURLcode r = curl_easy_perform(c);
    struct curl_slist *resolve = NULL;
    for (int tries = 0; r == CURLE_COULDNT_RESOLVE_HOST && tries < 4 && !tr_cancel; tries++) {
        char *eff = NULL, host[256], ip[32], entry[320];
        curl_easy_getinfo(c, CURLINFO_EFFECTIVE_URL, &eff);
        url_host(eff ? eff : url, host, sizeof(host));
        if (!host[0] || !dns_fallback(host, ip, sizeof(ip))) break;
        snprintf(entry, sizeof(entry), "%s:80:%s", host, ip); resolve = curl_slist_append(resolve, entry);
        snprintf(entry, sizeof(entry), "%s:443:%s", host, ip); resolve = curl_slist_append(resolve, entry);
        curl_easy_setopt(c, CURLOPT_RESOLVE, resolve);
        r = curl_easy_perform(c);
    }
    if (r != CURLE_OK && r != CURLE_HTTP_RETURNED_ERROR) snprintf(tr_err, sizeof(tr_err), "curl %d: %s", (int)r, curl_easy_strerror(r));
    curl_easy_cleanup(c);
    if (resolve) curl_slist_free_all(resolve);
    fclose(f);
    return r == CURLE_OK;
}

/* "key":"value" after p (handles \/), returns pointer after the value or NULL */
static const char *json_str(const char *p, const char *key, char *out, int n) {
    char pat[64]; snprintf(pat, sizeof(pat), "\"%s\":\"", key);
    const char *q = strstr(p, pat); if (!q) return NULL;
    q += strlen(pat); int j = 0;
    while (*q && *q != '"' && j < n - 1) {
        if (*q == '\\' && q[1]) { q++; if (*q == 'u' && q[1] && q[2] && q[3] && q[4]) { q += 5; out[j++] = '?'; continue; } }
        out[j++] = *q++;
    }
    out[j] = 0; return q;
}
static void norm_name(const char *s, char *o, int n) {
    int j = 0;
    for (; *s && j < n - 4; s++) {
        unsigned char ch = *s;
        if (ch == '&') { o[j++] = 'a'; o[j++] = 'n'; o[j++] = 'd'; continue; }
        if (ch >= 'A' && ch <= 'Z') ch += 32;
        if ((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')) o[j++] = ch;
    }
    o[j] = 0;
}

/* same game? exact name, or the name plus only edition words ("Director's Cut Edition", "GOTY", "Remastered"...) */
static int name_ok(const char *nn, const char *want) {
    size_t lw = strlen(want);
    if (!strcmp(nn, want)) return 2;
    if (lw < 4 || strncmp(nn, want, lw)) return 0;
    static const char *words[] = { "the", "directorscut", "directors", "cut", "edition", "complete", "goty", "gameoftheyear",
        "definitive", "remastered", "remaster", "hd", "deluxe", "ultimate", "gold", "collection", "enhanced", "special",
        "anniversary", "classic", "classics", "standard", "digital", "premium", "royal", "legacy", "redux", "dx", "plus",
        "for", "pc", "windows", "steam", "complete", "bundle", "trilogy", "x", "s", NULL };
    const char *r = nn + lw;
    while (*r) {
        int hit = 0;
        for (int i = 0; words[i]; i++) { size_t l = strlen(words[i]); if (!strncmp(r, words[i], l)) { r += l; hit = 1; break; } }
        if (!hit) return 0;
    }
    return 1;
}

static int rawg_trailer(const char *name, char *url, int n) {
    if (!rawg_key[0]) return 0;
    static char json[256 * 1024];
    char q[400], u[700];
    url_encode(name, q, sizeof(q));
    snprintf(u, sizeof(u), "https://api.rawg.io/api/games?key=%s&search=%s&page_size=5&search_precise=true", rawg_key, q);
    MemBuf m = { json, 0, sizeof(json) }; json[0] = 0;
    if (!http_get(u, NULL, &m)) return 0;
    const char *r = strstr(json, "\"results\":["); if (!r) return 0;
    char want[200], nm[300], nn[300]; norm_name(name, want, sizeof(want));
    long id = 0; int best = 0;
    /* each result: "id":N,"slug":"...","name":"..." */
    for (const char *p = r; (p = strstr(p, "\"slug\":\"")); ) {
        /* only top-level results look like {"id":123,"slug":"..","name":".."} */
        const char *idp = p; int steps = 0; while (idp > r && strncmp(idp, "\"id\":", 5) && steps++ < 24) idp--;
        long rid = 0;
        if (idp > r && !strncmp(idp, "\"id\":", 5)) {
            const char *d = idp + 5; while (d < p && *d >= '0' && *d <= '9') d++;
            if (*d == ',' && d + 1 == p) rid = atol(idp + 5);
        }
        if (!rid) { p += 8; continue; }
        const char *after = json_str(p, "name", nm, sizeof(nm)); if (!after) break;
        norm_name(nm, nn, sizeof(nn));
        int k = name_ok(nn, want);
        if (k > best && rid) { best = k; id = rid; if (k == 2) break; }
        p = after;
    }
    if (!id) return 0;
    snprintf(u, sizeof(u), "https://api.rawg.io/api/games/%ld/movies?key=%s", id, rawg_key);
    m.len = 0; json[0] = 0;
    if (!http_get(u, NULL, &m)) return 0;
    const char *d = strstr(json, "\"data\":{"); if (!d) return 0;
    return json_str(d, "480", url, n) || json_str(d, "max", url, n);
}

/* keep the 6 most recent trailers on disk */
static void tr_cache_add(const char *key) {
    char idx[300]; snprintf(idx, sizeof(idx), TR_DIR "/index.txt");
    char *old = read_file(idx, NULL);
    char keys[16][48]; int n = 0;
    if (old) { char *s = old, *t; while ((t = strtok(s, "\r\n")) && n < 16) { s = NULL; if (strcmp(t, key)) snprintf(keys[n++], 48, "%s", t); } free(old); }
    while (n >= 6) { char p[300]; snprintf(p, sizeof(p), TR_DIR "/%s.mp4", keys[0]); unlink(p); memmove(keys[0], keys[1], sizeof(keys[0]) * (n - 1)); n--; }
    snprintf(keys[n++], 48, "%s", key);
    FILE *f = fopen(idx, "w"); if (!f) return;
    for (int i = 0; i < n; i++) fprintf(f, "%s\n", keys[i]);
    fclose(f);
}

static void tr_entry(void *arg) {
    static char url[2048]; url[0] = 0;
    mkdir(APPDIR "USRDIR", 0777); mkdir(TR_DIR, 0777);
    snprintf(tr_path, sizeof(tr_path), TR_DIR "/%s.mp4", tr_key);
    if (!file_exists(tr_path)) {
        tr_state = TR_SEARCH;
        int fails0 = http_fails;
        /* 1) the store's own trailers (uploaded with Arcadia Studio), 2) RAWG */
        if (vid_base[0]) snprintf(url, 2048, "%s%s.mp4", vid_base, tr_key);
        if (url[0]) {
            tr_state = TR_DOWNLOAD; tr_now = tr_total = 0;
            char part0[320]; snprintf(part0, sizeof(part0), "%s.part", tr_path);
            int ok0 = http_get_progress(url, part0);
            if (ok0) { FILE *g = fopen(part0, "rb"); u8 h8[8] = {0}; if (g) { if (fread(h8, 1, 8, g) != 8) h8[4] = 0; fclose(g); } ok0 = !memcmp(h8 + 4, "ftyp", 4); }
            if (ok0) { unlink(tr_path); rename(part0, tr_path); tr_cache_add(tr_key); tr_state = TR_READY; sysThreadExit(0); }
            unlink(part0); url[0] = 0; tr_err[0] = 0; tr_state = TR_SEARCH; fails0 = http_fails;
        }
        if (tr_cancel) { tr_state = TR_IDLE; sysThreadExit(0); }
        rawg_trailer(tr_name, url, 2048);
        if (tr_cancel) { tr_state = TR_IDLE; sysThreadExit(0); }
        if (!url[0]) {
            if (http_fails != fails0) { snprintf(tr_err, sizeof(tr_err), "%s", http_err); tr_state = TR_FAIL; }
            else tr_state = TR_NONE;
            sysThreadExit(0);
        }
        tr_state = TR_DOWNLOAD; tr_now = tr_total = 0;
        char part[320]; snprintf(part, sizeof(part), "%s.part", tr_path);
        int ok = 0; char *save = NULL;
        for (char *cand = strtok_r(url, "|", &save); cand && !ok && !tr_cancel; cand = strtok_r(NULL, "|", &save)) {
            tr_now = tr_total = 0;
            ok = http_get_progress(cand, part);
            if (ok) { FILE *g = fopen(part, "rb"); u8 h8[8] = {0}; if (g) { if (fread(h8, 1, 8, g) != 8) h8[4] = 0; fclose(g); } ok = !memcmp(h8 + 4, "ftyp", 4); }
        }
        if (!ok) { unlink(part); if (!tr_cancel && !tr_err[0]) snprintf(tr_err, sizeof(tr_err), "no mp4"); tr_state = tr_cancel ? TR_IDLE : (tr_err[0] && strcmp(tr_err, "no mp4") ? TR_FAIL : TR_NONE); sysThreadExit(0); }
        unlink(tr_path); rename(part, tr_path);
    }
    tr_cache_add(tr_key);
    tr_state = TR_READY;
    sysThreadExit(0);
}

/* ---------------- playback */
#define PL_FRAMES 6
#define PCM_RING (48000 * 3)          /* stereo frames */
typedef struct { u8 *px; int64_t pts90; volatile int full; } PlFrame;
static struct {
    int active, paused, failed;
    Mp4 mp4;
    int w, h, pitch;
    PlFrame fr[PL_FRAMES]; volatile int fr_w, fr_r;
    Tex tex[2]; int tex_cur, tex_ok;
    volatile int stop, vdone, adone;
    int64_t start90;                  /* media time of the first frame shown after a seek */
    /* audio */
    float *pcm; volatile u32 pcm_w, pcm_r;   /* frames, monotonic */
    volatile u64 audio_frames_played;
    u64 base_frames;                         /* audio frames already played before the current segment */
    u32 port; int port_open;
    sys_ppu_thread_t tv, ta, to;
    u32 vh; volatile int au_pending, pic_ready; int vdec_open;
    u8 *vmem;
    double dur;
    u32 show_osd;
    u32 clock_frames;                        /* fallback clock when there is no audio */
    int64_t last_pts90, frame90; int shown_any;
    volatile u32 n_sent, n_done, n_pic, n_get_err, n_err, last_err; u32 open_frame;
} pl;

static int64_t to90(const Mp4Track *t, int64_t v) { return v * 90000 / (t->timescale ? t->timescale : 90000); }

#ifndef HOST_PREVIEW
static u32 vdec_cb(u32 handle, u32 msgtype, u32 msgdata, u32 arg) {
    if (msgtype == VDEC_CALLBACK_AUDONE) { if (pl.au_pending > 0) pl.au_pending--; pl.n_done++; }
    else if (msgtype == VDEC_CALLBACK_PICOUT) { pl.pic_ready++; pl.n_pic++; }
    else if (msgtype == VDEC_CALLBACK_ERROR) { pl.n_err++; pl.last_err = msgdata; }
    return 0;
}
#endif

/* pull every finished picture into the frame queue */
static void pl_collect(void) {
#ifndef HOST_PREVIEW
    while (pl.pic_ready > 0 && !pl.stop) {
        PlFrame *f = &pl.fr[pl.fr_w % PL_FRAMES];
        while (f->full && !pl.stop) usleep(2000);
        if (pl.stop) return;
        u32 item = 0; int64_t pts = 0;
        if (vdecGetPicItem(pl.vh, &item) == 0 && item) {
            vdecPicture *pi = (vdecPicture *)(u64)item;
            pts = ((int64_t)pi->pts[0].hi << 32) | pi->pts[0].low;
        }
        vdecPictureFormat fmt = { VDEC_PICFMT_ARGB32, VDEC_COLOR_MATRIX_BT709, 0xFF };
        int gr = vdecGetPicture(pl.vh, &fmt, f->px);
        if (gr != 0) { pl.n_get_err++; pl.last_err = (u32)gr; pl.pic_ready = 0; return; }
        pl.pic_ready--;
        if (pts <= pl.last_pts90 || (u32)pts == 0xFFFFFFFF) pts = pl.last_pts90 + pl.frame90;   /* missing/odd timestamps */
        pl.last_pts90 = pts;
        f->pts90 = pts; f->full = 1; pl.fr_w++;
    }
#endif
}

/* video decode thread: AVCC samples -> Annex-B access units -> hardware decoder */
static void pl_video(void *arg) {
    Mp4Track *v = &pl.mp4.video;
    u32 maxs = 0; for (u32 i = 0; i < v->count; i++) if (v->s[i].size > maxs) maxs = v->s[i].size;
    const int SLOTS = 8;
    u32 slot_sz = maxs + v->sps_pps_len + 64 * 4 + 64;
    u8 *ring = memalign(128, (size_t)slot_sz * SLOTS), *tmp = malloc(maxs + 16);
    FILE *vf = fopen(tr_path, "rb");
    u32 start = (u32)(u64)arg, k = 0;
    for (u32 i = start; i < v->count && !pl.stop && ring && tmp; i++, k++) {
        Mp4Sample *s = &v->s[i];
        u8 *au = ring + (size_t)(k % SLOTS) * slot_sz; u32 al = 0;
        if (!mp4_read_fp(vf, s, tmp)) break;
        if (s->key) { memcpy(au, v->sps_pps, v->sps_pps_len); al = v->sps_pps_len; }
        u32 p = 0;
        while (p + v->nal_len <= s->size) {
            u32 l = 0; for (int b = 0; b < v->nal_len; b++) l = (l << 8) | tmp[p + b];
            p += v->nal_len;
            if (p + l > s->size || al + 4 + l > slot_sz) break;
            au[al] = 0; au[al + 1] = 0; au[al + 2] = 0; au[al + 3] = 1; memcpy(au + al + 4, tmp + p, l); al += 4 + l; p += l;
        }
#ifndef HOST_PREVIEW
        while (pl.au_pending >= SLOTS - 1 && !pl.stop) { pl_collect(); usleep(1000); }
        int64_t pts = to90(v, s->dts + s->cts), dts = to90(v, s->dts);
        vdecAU a; memset(&a, 0, sizeof(a));
        a.packet_addr = (u32)(u64)au; a.packet_size = al;
        a.pts.low = (u32)pts; a.pts.hi = (u32)(pts >> 32); a.dts.low = (u32)dts; a.dts.hi = (u32)(dts >> 32);
        pl.au_pending++;
        int r, tries = 0;
        while ((r = vdecDecodeAu(pl.vh, VDEC_DECODER_MODE_NORMAL, &a)) == (int)VDEC_ERROR_BUSY && !pl.stop && tries++ < 2000) { pl_collect(); usleep(1000); }
        if (r) { pl.au_pending--; pl.n_err++; pl.last_err = (u32)r; } else pl.n_sent++;
        pl_collect();
#else
        /* host preview only: frames come from ffmpeg instead of the PS3 decoder */
        (void)al;
        static FILE *ff; static u32 ff_start = 0xFFFFFFFF;
        if (ff_start != start) {
            if (ff) pclose(ff);
            char cmd[600]; snprintf(cmd, sizeof(cmd), "ffmpeg -v error -ss %.3f -i '%s' -an -f rawvideo -pix_fmt bgra -", (double)to90(v, v->s[start].dts) / 90000.0, tr_path);
            ff = popen(cmd, "r"); ff_start = start;
        }
        PlFrame *f = &pl.fr[pl.fr_w % PL_FRAMES];
        while (f->full && !pl.stop) usleep(2000);
        if (pl.stop) break;
        if (!ff || fread(f->px, 1, (size_t)pl.pitch * pl.h, ff) != (size_t)pl.pitch * pl.h) break;
        f->pts90 = pl.last_pts90 + pl.frame90; pl.last_pts90 = f->pts90; f->full = 1; pl.fr_w++;
#endif
    }
#ifndef HOST_PREVIEW
    for (int t = 0; t < 400 && !pl.stop && (pl.au_pending > 0 || pl.pic_ready > 0); t++) { pl_collect(); usleep(5000); }
#endif
    free(ring); free(tmp); if (vf) fclose(vf);
    pl.vdone = 1;
    sysThreadExit(0);
}

/* audio decode thread: AAC -> 48 kHz stereo float ring */
static void pl_audio(void *arg) {
    Mp4Track *a = &pl.mp4.audio;
    NeAACDecHandle h = NeAACDecOpen();
    NeAACDecConfigurationPtr cfg = NeAACDecGetCurrentConfiguration(h);
    cfg->outputFormat = FAAD_FMT_FLOAT; cfg->downMatrix = 1; NeAACDecSetConfiguration(h, cfg);
    unsigned long rate = 0; unsigned char ch = 0;
    if (NeAACDecInit2(h, a->asc, a->asc_len, &rate, &ch) < 0 || !rate) { NeAACDecClose(h); pl.adone = 1; sysThreadExit(0); }
    u32 maxs = 0; for (u32 i = 0; i < a->count; i++) if (a->s[i].size > maxs) maxs = a->s[i].size;
    u8 *buf = malloc(maxs + 16);
    FILE *af = fopen(tr_path, "rb");
    double step = (double)rate / 48000.0, pos = 0;     /* linear resampler */
    float last[2] = { 0, 0 };
    for (u32 i = (u32)(u64)arg; i < a->count && !pl.stop && buf; i++) {
        if (!mp4_read_fp(af, &a->s[i], buf)) break;
        NeAACDecFrameInfo fi;
        float *pcm = (float *)NeAACDecDecode(h, &fi, buf, a->s[i].size);
        if (fi.error || !pcm || !fi.samples) continue;
        int chn = fi.channels ? fi.channels : 1, nfr = fi.samples / chn;
        /* resample this block */
        while (!pl.stop) {
            double ip = pos; int i0 = (int)ip; if (i0 >= nfr) { pos -= nfr; break; }
            float fr = (float)(ip - i0);
            float l0 = i0 == 0 ? last[0] : pcm[(i0 - 1) * chn], r0 = i0 == 0 ? last[1] : pcm[(i0 - 1) * chn + (chn > 1)];
            float l1 = pcm[i0 * chn], r1 = pcm[i0 * chn + (chn > 1)];
            (void)l0; (void)r0;
            while (pl.pcm_w - pl.pcm_r >= PCM_RING - 1 && !pl.stop) usleep(4000);
            u32 w = pl.pcm_w % PCM_RING;
            pl.pcm[w * 2] = l0 + (l1 - l0) * fr; pl.pcm[w * 2 + 1] = r0 + (r1 - r0) * fr;
            pl.pcm_w++;
            pos += step;
        }
        last[0] = pcm[(nfr - 1) * chn]; last[1] = pcm[(nfr - 1) * chn + (chn > 1)];
    }
    free(buf); NeAACDecClose(h); if (af) fclose(af);
    pl.adone = 1;
    sysThreadExit(0);
}

#ifndef HOST_PREVIEW
static sys_event_queue_t snd_queue; static u64 snd_key;
/* audio output thread: feeds the hardware port block by block */
static void pl_out(void *arg) {
    audioPortConfig cfg; audioGetPortConfig(pl.port, &cfg);
    f32 *data = (f32 *)(u64)cfg.audioDataStart;
    while (!pl.stop) {
        sys_event_t ev;
        if (sysEventQueueReceive(snd_queue, &ev, 20 * 1000) != 0) continue;
        u64 cur = *(u64 *)(u64)cfg.readIndex;
        f32 *b = data + cfg.channelCount * AUDIO_BLOCK_SAMPLES * ((cur + 2) % cfg.numBlocks);
        for (int i = 0; i < AUDIO_BLOCK_SAMPLES; i++) {
            if (!pl.paused && pl.pcm_r < pl.pcm_w) {
                u32 r = pl.pcm_r % PCM_RING; b[i * 2] = pl.pcm[r * 2]; b[i * 2 + 1] = pl.pcm[r * 2 + 1]; pl.pcm_r++;
                pl.audio_frames_played++;
            } else { b[i * 2] = 0; b[i * 2 + 1] = 0; }
        }
    }
    sysThreadExit(0);
}
#endif

static int64_t pl_clock90(void) {
    if (pl.mp4.audio.present && pl.port_open) return pl.start90 + (int64_t)((pl.audio_frames_played) * 90000 / 48000);
    return pl.start90 + (int64_t)pl.clock_frames * 1500;      /* 60 fps display */
}

static void pl_stop_threads(void) {
    pl.stop = 1;
    for (int i = 0; i < 200 && !(pl.vdone && (pl.adone || !pl.mp4.audio.present)); i++) usleep(10000);
#ifndef HOST_PREVIEW
    if (pl.vdec_open) { vdecEndSequence(pl.vh); usleep(20000); }
#endif
}

/* start (or restart after a seek) decoding from video sample `vi` */
static void pl_start_at(u32 vi) {
    Mp4Track *v = &pl.mp4.video, *a = &pl.mp4.audio;
    while (vi > 0 && !v->s[vi].key) vi--;
    pl.stop = 0; pl.vdone = 0; pl.adone = !a->present;
    for (int i = 0; i < PL_FRAMES; i++) pl.fr[i].full = 0;
    pl.fr_w = pl.fr_r = 0; pl.au_pending = 0; pl.pic_ready = 0;
    pl.pcm_w = pl.pcm_r = 0; pl.audio_frames_played = 0; pl.clock_frames = 0;
    pl.start90 = to90(v, v->s[vi].dts + v->s[vi].cts);
    pl.last_pts90 = pl.start90 - pl.frame90; pl.shown_any = 0;
    u32 ai = 0;
    if (a->present) {
        int64_t want = (int64_t)pl.start90 * a->timescale / 90000;
        while (ai + 1 < a->count && a->s[ai + 1].dts <= want) ai++;
    }
#ifndef HOST_PREVIEW
    if (pl.vdec_open) vdecStartSequence(pl.vh);
#endif
    sysThreadCreate(&pl.tv, pl_video, (void *)(u64)vi, 1000, 0x10000, 0, "pl_video");
    if (a->present) sysThreadCreate(&pl.ta, pl_audio, (void *)(u64)ai, 1000, 0x20000, 0, "pl_audio");
}

static void player_close(void);
static int player_open(const char *path) {
    memset(&pl, 0, sizeof(pl));
    char err[100];
    if (!mp4_open(&pl.mp4, path, err, sizeof(err))) { snprintf(tr_err, sizeof(tr_err), "mp4: %s", err); return 0; }
    Mp4Track *v = &pl.mp4.video;
    pl.w = v->width; pl.h = v->height;
    if (pl.w < 16 || pl.h < 16 || pl.w > 1920 || pl.h > 1088) { snprintf(tr_err, sizeof(tr_err), "size %dx%d", pl.w, pl.h); mp4_close(&pl.mp4); return 0; }
    pl.pitch = pl.w * 4;
    pl.frame90 = v->count > 1 ? to90(v, v->s[1].dts - v->s[0].dts) : 3000; if (pl.frame90 <= 0) pl.frame90 = 3000;
    pl.dur = v->count ? (double)(v->s[v->count - 1].dts) / v->timescale : 0;
    for (int i = 0; i < PL_FRAMES; i++) pl.fr[i].px = memalign(128, (size_t)pl.pitch * pl.h);
    /* two textures sized for the video (taken once from the pool) */
    static Tex pt[2]; static int pt_w, pt_h;
    if (pt_w < pl.w || pt_h < pl.h) {
        int tw = pl.w > 864 ? 1280 : 864, th = pl.h > 480 ? 720 : 480;
        if (pt_w < tw || pt_h < th) { tex_make(&pt[0], tw, th); tex_make(&pt[1], tw, th); pt_w = tw; pt_h = th; }
    }
    pl.tex[0] = pt[0]; pl.tex[1] = pt[1];
    pl.tex_ok = pt[0].mem && pt[1].mem && pt_w >= pl.w && pt_h >= pl.h;
    if (!pl.tex_ok) { snprintf(tr_err, sizeof(tr_err), "no texture memory"); player_close(); return 0; }
#ifndef HOST_PREVIEW
    vdecType t = { VDEC_CODEC_TYPE_H264, 42 };
    vdecAttr at; memset(&at, 0, sizeof(at));
    if (vdecQueryAttr(&t, &at) != 0) { snprintf(tr_err, sizeof(tr_err), "vdecQueryAttr"); player_close(); return 0; }
    pl.vmem = memalign(1024, at.mem_size);
    vdecConfig c; memset(&c, 0, sizeof(c));
    c.mem_addr = (u32)(u64)pl.vmem; c.mem_size = at.mem_size; c.ppu_thread_prio = 400; c.ppu_thread_stack_size = 0x4000;
    c.spu_thread_prio = 200; c.num_spus = 1;
    vdecClosure cl = { (u32)__get_opd32(vdec_cb), 0 };
    int r = vdecOpen(&t, &c, &cl, &pl.vh);
    if (r) { snprintf(tr_err, sizeof(tr_err), "vdecOpen %08x", r); player_close(); return 0; }
    pl.vdec_open = 1;
    if (pl.mp4.audio.present) {
        pl.pcm = malloc(sizeof(float) * 2 * PCM_RING);
        audioPortParam pp = { AUDIO_PORT_2CH, AUDIO_BLOCK_8, AUDIO_PORT_INITLEVEL, 1.0f };
        if (pl.pcm && audioPortOpen(&pp, &pl.port) == 0) {
            audioCreateNotifyEventQueue(&snd_queue, &snd_key);
            audioSetNotifyEventQueue(snd_key);
            sysEventQueueDrain(snd_queue);
            audioPortStart(pl.port);
            pl.port_open = 1;
            sysThreadCreate(&pl.to, pl_out, NULL, 900, 0x4000, 0, "pl_out");
        } else pl.mp4.audio.present = 0;
    }
#else
    pl.mp4.audio.present = 0;
#endif
    pl.active = 1; pl.show_osd = frame_no + 240; pl.open_frame = frame_no;
    pl_start_at(0);
    return 1;
}

static void player_close(void) {
    pl_stop_threads();
#ifndef HOST_PREVIEW
    if (pl.port_open) {
        usleep(30000);
        audioPortStop(pl.port); audioRemoveNotifyEventQueue(snd_key); audioPortClose(pl.port); sysEventQueueDestroy(snd_queue, 0);
        pl.port_open = 0;
    }
    if (pl.vdec_open) { vdecClose(pl.vh); pl.vdec_open = 0; }
#endif
    for (int i = 0; i < PL_FRAMES; i++) free(pl.fr[i].px);
    free(pl.vmem); free(pl.pcm);
    mp4_close(&pl.mp4);
    pl.active = 0;
}

static void player_seek(double secs) {
    Mp4Track *v = &pl.mp4.video;
    int64_t target = (int64_t)((pl_clock90() / 90000.0 + secs) * v->timescale);
    if (target < 0) target = 0;
    pl_stop_threads();
    u32 vi = 0; while (vi + 1 < v->count && v->s[vi + 1].dts <= target) vi++;
    pl_start_at(vi);
    pl.show_osd = frame_no + 180;
}

static void fmt_time(double s, char *o, int n) { int t = (int)s; snprintf(o, n, "%d:%02d", t / 60, t % 60); }

static void player_frame(void) {
    if (!pl.paused) pl.clock_frames++;
    int64_t now = pl_clock90();
    /* newest frame that is due */
    PlFrame *show = NULL;
    while (pl.fr_r < pl.fr_w) {
        PlFrame *f = &pl.fr[pl.fr_r % PL_FRAMES];
        if (!f->full) break;
        PlFrame *nx = pl.fr_r + 1 < pl.fr_w ? &pl.fr[(pl.fr_r + 1) % PL_FRAMES] : NULL;
        if (f->pts90 > now && (show || pl.shown_any)) break;
        if (show) show->full = 0;
        show = f; pl.fr_r++;
        if (!nx || !nx->full || nx->pts90 > now) break;
    }
    if (show) {
        Tex *t = &pl.tex[pl.tex_cur ^ 1];
        for (int y = 0; y < pl.h; y++) memcpy(t->mem + (size_t)y * t->w * 4, show->px + (size_t)y * pl.pitch, pl.pitch);
        t->used_w = pl.w; t->used_h = pl.h; pl.tex_cur ^= 1;
        show->full = 0; pl.shown_any = 1;
    }
    draw_quad(0, 0, SW, SH, 0x000000FF);
    Tex *t = &pl.tex[pl.tex_cur];
    if (t->used_w) {
        float ar = (float)pl.w / pl.h, w = SW, h = SW / ar;
        if (h > SH) { h = SH; w = SH * ar; }
        /* the decoder's alpha byte is ignored: draw the picture opaque (no alpha test, no blending) */
        tiny3d_AlphaTest(0, 0x00, TINY3D_ALPHA_FUNC_ALWAYS);
        tiny3d_BlendFunc(0, TINY3D_BLEND_FUNC_SRC_RGB_SRC_ALPHA | TINY3D_BLEND_FUNC_SRC_ALPHA_SRC_ALPHA,
                         TINY3D_BLEND_FUNC_DST_RGB_ONE_MINUS_SRC_ALPHA | TINY3D_BLEND_FUNC_DST_ALPHA_ZERO,
                         TINY3D_BLEND_RGB_FUNC_ADD | TINY3D_BLEND_ALPHA_FUNC_ADD);
        draw_tex_uv(t, (SW - w) / 2, (SH - h) / 2, w, h, 0, 0, (float)t->used_w / t->w, (float)t->used_h / t->h, 0xFFFFFFFF);
        tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
        tiny3d_BlendFunc(1, TINY3D_BLEND_FUNC_SRC_RGB_SRC_ALPHA | TINY3D_BLEND_FUNC_SRC_ALPHA_SRC_ALPHA,
                         TINY3D_BLEND_FUNC_DST_RGB_ONE_MINUS_SRC_ALPHA | TINY3D_BLEND_FUNC_DST_ALPHA_ZERO,
                         TINY3D_BLEND_RGB_FUNC_ADD | TINY3D_BLEND_ALPHA_FUNC_ADD);
    } else text_c(S_TR_PREP, SW / 2, SH / 2, 13, C_SOFT, 0);
    /* decoder status: always while no picture has been shown, and with the controls */
    if (!t->used_w || pl.paused || frame_no < pl.show_osd) {
        char d[160];
        snprintf(d, sizeof(d), "%dx%d  AU %u/%u/%u  PIC %u  ERR %u %08X  AUD %s %u",
                 pl.w, pl.h, pl.n_sent, pl.n_done, pl.mp4.video.count, pl.n_pic, pl.n_err + pl.n_get_err, pl.last_err,
                 pl.port_open ? "on" : "off", (u32)(pl.audio_frames_played / 48000));
        draw_text(d, LX, 24, 8.5f, 0xFFFFFF99, 0);
    }
    /* on-screen controls */
    int done = pl.vdone && pl.fr_r >= pl.fr_w;
    if (pl.paused || frame_no < pl.show_osd || done) {
        draw_grad(0, SH - 96, SW, 96, 0x00000000, 0x000000E0);
        double cur = now / 90000.0; if (cur > pl.dur) cur = pl.dur;
        float bx = LX, bw = SW - 2 * LX, by = SH - 58;
        draw_rrect(bx, by, bw, 6, 3, 0xFFFFFF33);
        if (pl.dur > 0) draw_rrect(bx, by, bw * (float)(cur / pl.dur), 6, 3, C_ACCENT);
        char a[16], b[16], tt[40]; fmt_time(cur, a, sizeof(a)); fmt_time(pl.dur, b, sizeof(b)); snprintf(tt, sizeof(tt), "%s / %s", a, b);
        draw_text(tt, LX, by - 10, 10, C_TEXT, 0);
        text_rb(tr_title, RX, by - 10, 12, C_TEXT, 500);
        float x = RX, cy = SH - 28;
        glyph_x(x - 8.5f, cy, 8.5f, 0); x -= 22; text_r(pl.paused ? S_PLAY : S_PAUSE, x, cy + 3.6f, 10, C_SOFT, 0); x -= text_width(pl.paused ? S_PLAY : S_PAUSE, 10) + 22;
        glyph_o(x - 8.5f, cy, 8.5f); x -= 22; text_r(S_CLOSE, x, cy + 3.6f, 10, C_SOFT, 0); x -= text_width(S_CLOSE, 10) + 22;
        draw_text("\xE2\x86\x90 \xE2\x86\x92", x - 30, cy + 3.6f, 10, C_TEXT, 0); x -= 38; text_r(S_SEEK, x, cy + 3.6f, 10, C_SOFT, 0);
    }
}

static void trailer_open(Node *n, const char *search) {
    if (tr_state == TR_SEARCH || tr_state == TR_DOWNLOAD) return;
    key_of(n->title, n->info, tr_key, sizeof(tr_key));
    snprintf(tr_name, sizeof(tr_name), "%s", search ? search : n->title);
    snprintf(tr_title, sizeof(tr_title), "%s", has_arabic(n->title) && search ? search : n->title);
    tr_cancel = 0; tr_err[0] = 0; tr_state = TR_SEARCH;
    if (sysThreadCreate(&tr_thread, tr_entry, NULL, 1100, 0x10000, 0, "trailer") != 0) tr_state = TR_FAIL;
}

/* loading screen while the trailer is searched / downloaded; returns 1 while it covers the screen */
static int trailer_draw(void) {
    if (pl.active) { player_frame(); return 1; }
    if (tr_state == TR_READY) {
        tr_state = TR_IDLE;
        if (!player_open(tr_path)) { char t[300]; snprintf(t, sizeof(t), "(%s)  %s", tr_err, S_TR_FAIL); show_toast(t); unlink(tr_path); }
        return pl.active;
    }
    if (tr_state == TR_NONE) { tr_state = TR_IDLE; show_toast(S_TR_NONE); return 0; }
    if (tr_state == TR_FAIL) { tr_state = TR_IDLE; char t[300]; snprintf(t, sizeof(t), "(%s)  %s", tr_err, S_TR_FAIL); show_toast(t); return 0; }
    if (tr_state != TR_SEARCH && tr_state != TR_DOWNLOAD) return 0;
    draw_quad(0, 0, SW, SH, 0x04050CE6);
    float w = 420, h = 120, x = (SW - w) / 2, y = (SH - h) / 2;
    draw_rrect(x, y, w, h, 16, 0x12152EFF);
    text_rb(tr_title, x + w - 24, y + 36, 15, C_TEXT, w - 48);
    if (tr_state == TR_SEARCH) text_r(S_TR_SEARCH, x + w - 24, y + 64, 10.5f, C_GOLD, 0);
    else {
        float p = tr_total > 0 ? (float)(tr_now / tr_total) : 0;
        char t[80]; snprintf(t, sizeof(t), "%d%%   %.1f / %.1f MB", (int)(p * 100), tr_now / 1048576.0, tr_total / 1048576.0);
        text_r(S_TR_DL, x + w - 24, y + 64, 10.5f, C_GOLD, 0);
        draw_rrect(x + 24, y + 76, w - 48, 8, 4, 0xFFFFFF1A);
        if (p > 0.01f) draw_rrect(x + 24 + (w - 48) * (1 - p), y + 76, (w - 48) * p, 8, 4, C_ACCENT);
        draw_text(t, x + 24, y + 104, 9.5f, 0xCFCBE6FF, 0);
    }
    glyph_o(x + 32, y + 30, 8); draw_text(S_CANCEL3, x + 46, y + 34, 9.5f, C_SOFT, 0);
    return 1;
}

/* input while the trailer covers the screen; returns 1 when it consumed the input */
static int trailer_input(void) {
    if (pl.active) {
        if (pressed(K_O)) { player_close(); return 1; }
        if (pressed(K_X)) {
            if (pl.vdone && pl.fr_r >= pl.fr_w) player_seek(-1e9);      /* ended: play again */
            else pl.paused = !pl.paused;
            pl.show_osd = frame_no + 180;
        }
        if (pressed(K_RIGHT)) player_seek(10);
        if (pressed(K_LEFT)) player_seek(-10);
        return 1;
    }
    if (tr_state == TR_SEARCH || tr_state == TR_DOWNLOAD) { if (pressed(K_O)) tr_cancel = 1; return 1; }
    return 0;
}

/* ------------------------------------------------------------------ game page */
static int gp_open;
static void draw_game(Level *L) {
    Node *n = &L->items[L->sel];
    draw_backdrop(n->pic ? n->pic : L->pic, 1);
    char c[600]; crumbs(depth, c, sizeof(c));
    text_r(c, RX, 38, 9.3f, C_MUTED, 560);
    draw_dl_pill(NULL);

    /* video when the game has one, else the cover */
    const char *search = has_arabic(n->title) && L->title ? L->title : n->title;
    Tex *vt = video_for(n, search);
    float cs = 238, cx = RX - cs, cy = 58;
    char skey[48]; key_of(n->title, n->info, skey, sizeof(skey));
    int ns = vt ? 0 : shots_count(skey);
    if (!vt && ns) {
        /* slideshow of RAWG screenshots, 4 s each with a cross-fade */
        float vw = 320, vh = 180; cx = RX - vw;
        u32 per = 240, t = frame_no % (per * ns), i = t / per, in = t % per;
        char pa[300], pb[300];
        snprintf(pa, sizeof(pa), SHOT_DIR "/%s_%d.jpg", skey, (int)i);
        snprintf(pb, sizeof(pb), SHOT_DIR "/%s_%d.jpg", skey, (int)((i + 1) % ns));
        draw_rrect(cx - 3, cy - 3, vw + 6, vh + 6, 14, 0x000000B0);
        Tex *ta = get_image(icons, ICON_SLOTS, pa), *tb = ns > 1 ? get_image(icons, ICON_SLOTS, pb) : NULL;
        if (ta) draw_image_fill(ta, cx, cy, vw, vh, 0xFFFFFFFF);
        if (tb && in > per - 40) draw_image_fill(tb, cx, cy, vw, vh, 0xFFFFFF00 | (u32)((in - (per - 40)) * 255 / 40));
        for (int k = 0; k < ns; k++) draw_circle(cx + vw / 2 + (k - (ns - 1) / 2.0f) * 10, cy + vh - 8, 2.5f, k == (int)i ? 0xFFFFFFFF : 0xFFFFFF66);
        Tex *t2 = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        if (t2) { draw_rrect(RX - 70, cy + vh - 46, 62, 62, 9, 0x000000C0); draw_image_fit(t2, RX - 68, cy + vh - 44, 58, 58, 0xFFFFFFFF); }
        text_r("RAWG.io", RX - 80, cy + vh + 16, 8, 0x8F89B3FF, 0);
    } else if (vt) {
        float vw = 320, vh = 180; cx = RX - vw;
        draw_rrect(cx - 3, cy - 3, vw + 6, vh + 6, 14, 0x000000B0);
        draw_tex_uv(vt, cx, cy, vw, vh, 0, 0, (float)vt->used_w / vt->w, (float)vt->used_h / vt->h, 0xFFFFFFFF);
        Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;      /* small cover over the corner */
        if (t) { draw_rrect(RX - 70, cy + vh - 46, 62, 62, 9, 0x000000C0); draw_image_fit(t, RX - 68, cy + vh - 44, 58, 58, 0xFFFFFFFF); }
        text_r("RAWG.io  \xC2\xB7  ScreenScraper.fr", RX - 80, cy + vh + 16, 8, 0x8F89B3FF, 0);
    } else {
        draw_rrect(cx, cy, cs, cs, 16, 0x00000073);
        Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        if (t) draw_image_fit(t, cx + 6, cy + 6, cs - 12, cs - 12, 0xFFFFFFFF);
    }

    float r = cx - 32, mw = r - LX;
    const char *reg = region_of(L);
    char line[200];
    if (reg) snprintf(line, sizeof(line), "%s  \xC2\xB7  %s", reg, S_GAME); else snprintf(line, sizeof(line), "%s", S_GAME);
    text_r(line, r, 84, 10, C_GOLD, 0);
    text_rb(n->title, r, 118, 27, C_TEXT, mw);

    char id[64], size[64];
    int has = split_info(n->info, id, sizeof(id), size, sizeof(size));
    const char *lab[3] = { S_ID, S_SIZE, S_REGION };
    const char *val[3] = { has ? id : NULL, has ? size : NULL, reg };
    float bx = r, bw = (mw - 18) / 3;
    for (int k = 0; k < 3; k++) {
        draw_rrect(bx - bw, 140, bw, 50, 10, 0xFFFFFF0F);
        text_r(lab[k], bx - 11, 157, 8.6f, C_MUTED, 0);
        text_rb(val[k] ? val[k] : "-", bx - 11, 180, 12, C_TEXT, bw - 22);
        bx -= bw + 9;
    }
    if (!has && n->info) text_r(n->info, r, 210, 10, C_MUTED, mw);

    /* buttons */
    float w1 = text_width(S_DLINST, 12.5f) + 60, y = 214;
    draw_rrect(r - w1 - 4, y - 4, w1 + 8, 48, 14, 0x7C3AED40);
    draw_rrect(r - w1, y, w1, 40, 11, C_ACCENT);
    glyph_x(r - 24, y + 20, 9, 1);
    draw_text(S_DLINST, r - 40 - text_width(S_DLINST, 12.5f), y + 25, 12.5f, 0xFFFFFFFF, 0);
    float w2 = text_width(S_BACK, 11.5f) + 52, x2 = r - w1 - 10;
    draw_rrect(x2 - w2, y, w2, 40, 11, 0xFFFFFF14);
    glyph_o(x2 - 22, y + 20, 8);
    draw_text(S_BACK, x2 - 36 - text_width(S_BACK, 11.5f), y + 24.5f, 11.5f, C_TEXT, 0);

    float w3 = text_width(S_TRAILER, 11.5f) + 52, x3 = x2 - w2 - 10;
    draw_rrect(x3 - w3, y, w3, 40, 11, 0xFFFFFF14);
    glyph_sq(x3 - 22, y + 20, 8);
    draw_text(S_TRAILER, x3 - 36 - text_width(S_TRAILER, 11.5f), y + 24.5f, 11.5f, C_TEXT, 0);
    glyph_check(r - 7, 281, 12, C_MINT);
    text_r(S_INAPP, r - 20, 285, 10, 0xCFCBE6FF, mw - 20);

    /* neighbours */
    text_rb(S_SAME, RX, 330, 10.5f, C_SOFT, 0);
    int first = L->sel - 4; if (first < 0) first = 0;
    if (first + 9 > L->n) first = L->n - 9 < 0 ? 0 : L->n - 9;
    float nx = RX;
    for (int i = first; i < L->n && i < first + 9; i++) {
        Node *m = &L->items[i]; float s = 58;
        if (i == L->sel) draw_rrect(nx - s - 2, 340, s + 4, s + 4, 10, C_ACCENT);
        draw_rrect(nx - s, 342, s, s, 8, 0x000000AA);
        Tex *mt = m->icon ? get_image(icons, ICON_SLOTS, m->icon) : NULL;
        if (mt) draw_image_fit(mt, nx - s + 2, 344, s - 4, s - 4, i == L->sel ? 0xFFFFFFFF : 0xFFFFFFBB);
        nx -= s + 10;
    }
    Hint h[] = { {0, S_DLINST}, {4, S_TRAILER}, {1, S_BACK}, {3, S_DOWNLOADS} };
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", L->sel + 1, L->n);
    draw_footer(h, 4, pos);
}

/* ------------------------------------------------------------------ downloads screen */
static int dl_view, dl_sel, dl_top, exit_warn;
static int dl_order[MAX_JOBS], dl_count;

/* active ones first (in queue order), then finished ones, newest first */
static void dl_build_order(void) {
    dl_count = 0;
    for (int i = 0; i < MAX_JOBS; i++) if (jobs[i].used && jobs[i].state <= J_INSTALLING) dl_order[dl_count++] = i;
    for (int i = MAX_JOBS - 1; i >= 0; i--) if (jobs[i].used && jobs[i].state > J_INSTALLING) dl_order[dl_count++] = i;
    if (dl_sel >= dl_count) dl_sel = dl_count ? dl_count - 1 : 0;
}

static void draw_downloads(void) {
    dl_build_order();
    draw_backdrop(stack[0].pic, 1);
    char cnt[64]; snprintf(cnt, sizeof(cnt), "%s %d", S_ITEMS, dl_count);
    text_r(S_HOME, RX, 38, 9.3f, C_MUTED, 0);
    text_rb(S_DOWNLOADS, RX, 60, 18.5f, C_TEXT, 0);
    draw_dl_pill(cnt);

    if (!dl_count) {
        glyph_tri(SW / 2, 220, 22);
        text_c(S_NODL, SW / 2, 270, 13, C_SOFT, 0);
    }
    const float top = 92, rh = 68, gap = 8; const int vis = 5;
    if (dl_sel < dl_top) dl_top = dl_sel;
    if (dl_sel >= dl_top + vis) dl_top = dl_sel - vis + 1;
    int first_done = -1;
    for (int k = 0; k < dl_count; k++) if (jobs[dl_order[k]].state > J_INSTALLING) { first_done = k; break; }
    for (int k = dl_top; k < dl_count && k < dl_top + vis; k++) {
        Job *j = &jobs[dl_order[k]];
        float y = top + (k - dl_top) * (rh + gap);
        int f = k == dl_sel;
        if (f) { draw_rrect(LX - 2, y - 2, RX - LX + 4, rh + 4, 14, C_ACCENT); draw_rrect(LX, y, RX - LX, rh, 12, 0x221A50FF); }
        else draw_rrect(LX, y, RX - LX, rh, 12, k >= first_done && first_done >= 0 ? 0xFFFFFF0A : 0xFFFFFF12);

        float ix = RX - 10 - 50;
        draw_rrect(ix, y + 9, 50, 50, 9, 0x00000066);
        Tex *t = j->icon[0] ? get_image(icons, ICON_SLOTS, j->icon) : NULL;
        if (t) draw_image_fit(t, ix, y + 9, 50, 50, 0xFFFFFFFF);
        float tr = ix - 12;

        /* state chip on the left */
        const char *st; u32 sc, sbg;
        switch (j->state) {
        case J_QUEUED:      st = S_WAITING;    sc = C_SOFT;  sbg = 0xFFFFFF14; break;
        case J_DOWNLOADING: st = S_DLING;      sc = C_GOLD;  sbg = 0xFFD16624; break;
        case J_INSTALLING:  st = S_INSTALLING; sc = C_GOLD;  sbg = 0xFFD16624; break;
        case J_DONE:        st = S_INSTOK;     sc = C_MINT;  sbg = 0x2EA06733; break;
        case J_FAILED:      st = S_FAILED;     sc = 0xFF8A9AFF; sbg = 0xFF54702E; break;
        default:            st = S_CANCELED2;  sc = C_MUTED; sbg = 0xFFFFFF10; break;
        }
        float cw = text_width(st, 9.5f) + 22;
        draw_rrect(LX + 14, y + 22, cw, 24, 12, sbg);
        draw_text(st, LX + 25, y + 38, 9.5f, sc, 0);
        if (j->state == J_DONE) glyph_check(LX + 14 + cw + 12, y + 34, 12, C_MINT);
        float maxw = tr - (LX + 14 + cw + 30);

        text_rb(j->title, tr, y + 27, 13, C_TEXT, maxw);
        char line[200];
        if (j->state == J_DOWNLOADING || j->state == J_INSTALLING) {
            float pct = j->total > 0 ? (float)(j->now / j->total) : 0; if (pct > 1) pct = 1;
            if (j->state == J_DOWNLOADING && j->total > 0) snprintf(line, sizeof(line), "%d%%   %.1f / %.1f MB   \xC2\xB7   %.2f MB/s", (int)(pct * 100), j->now / 1048576.0, j->total / 1048576.0, j->speed / 1048576.0);
            else if (j->state == J_DOWNLOADING) snprintf(line, sizeof(line), "%.1f MB   \xC2\xB7   %.2f MB/s", j->now / 1048576.0, j->speed / 1048576.0);
            else snprintf(line, sizeof(line), "%d%%   %.1f / %.1f MB", (int)(pct * 100), j->now / 1048576.0, j->total / 1048576.0);
            text_r(line, tr, y + 44, 9.3f, 0xCFCBE6FF, maxw);
            float bw = maxw, bx = tr - bw;
            draw_rrect(bx, y + 52, bw, 6, 3, 0xFFFFFF1A);
            if (pct > 0.005f) draw_rrect(bx + bw * (1 - pct), y + 52, bw * pct, 6, 3, C_ACCENT);
        } else if (j->state == J_FAILED && j->err[0]) {
            snprintf(line, sizeof(line), "%s   \xC2\xB7   %s", j->err, j->info);
            text_r(line, tr, y + 48, 9.3f, 0xFF8A9AFF, maxw);
        } else text_r(j->info, tr, y + 48, 9.3f, C_MUTED, maxw);
    }
    if (dl_count > vis) {
        float th = vis * (rh + gap) - gap, bh = th * vis / dl_count, by = top + (th - bh) * dl_top / (dl_count - vis);
        draw_rrect(LX - 10, top, 3, th, 1.5f, 0xFFFFFF1F); draw_rrect(LX - 10, by, 3, bh, 1.5f, 0xFFFFFFAA);
    }

    Hint h[3]; int n = 0;
    if (dl_count) {
        Job *j = &jobs[dl_order[dl_sel]];
        if (j->state == J_FAILED || j->state == J_CANCELED) h[n++] = (Hint){0, S_RETRY};
        if (j->state == J_QUEUED || j->state == J_DOWNLOADING) h[n++] = (Hint){4, S_CANCEL3};
        else if (j->state != J_INSTALLING) h[n++] = (Hint){4, S_REMOVE};
    }
    h[n++] = (Hint){1, S_BACK};
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", dl_count ? dl_sel + 1 : 0, dl_count);
    draw_footer(h, n, pos);
}

static void downloads_input(void) {
    dl_build_order();
    if (pressed(K_O) || pressed(K_TRI)) { dl_view = 0; return; }
    if (!dl_count) return;
    if (pressed(K_DOWN)) { if (dl_sel + 1 < dl_count) dl_sel++; }
    if (pressed(K_UP)) { if (dl_sel > 0) dl_sel--; }
    Job *j = &jobs[dl_order[dl_sel]];
    if (pressed(K_X) && (j->state == J_FAILED || j->state == J_CANCELED)) {
        j->err[0] = 0; j->cancel = 0; j->notified = 0; j->now = j->total = 0; j->state = J_QUEUED; jobs_save();
    }
    if (pressed(K_SQ)) {
        if (j->state == J_DOWNLOADING) j->cancel = 1;
        else if (j->state == J_QUEUED) { j->state = J_CANCELED; j->notified = 1; jobs_save(); }
        else if (j->state != J_INSTALLING) { j->used = 0; jobs_save(); }
    }
}

static void draw_screen(void) {
    if (trailer_draw()) { draw_toast(); return; }
    if (depth < 0) {
        draw_backdrop(NULL, 1);
        text_c(S_NOSTORE, SW / 2, SH / 2, 14, C_TEXT, SW - 80);
        Hint h[] = { {1, S_EXITL} };
        draw_footer(h, 1, NULL);
        draw_toast();
        return;
    }
    Level *L = &stack[depth];
    if (!(gp_open && L->n && !dl_view) && vid_data) vid_unload();
    if (dl_view) draw_downloads();
    else if (gp_open && L->n) draw_game(L);
    else if (depth == 0) draw_home(L);
    else draw_grid(L);
    draw_toast();
}

/* ------------------------------------------------------------------ input */
static void open_item(Level *L) {
    Node *n = &L->items[L->sel];
    if (is_game(n) && !gp_open) { gp_open = 1; return; }   /* a game opens its page first */
    cur_info = n->info;
    activate(n);
}

static void handle_input(void) {
    if (exit_warn) exit_warn--;
    if (trailer_input()) return;
    if (depth < 0) { if (pressed(K_O) || pressed(K_X)) running = 0; return; }
    if (dl_view) { downloads_input(); return; }
    if (pressed(K_TRI)) { dl_view = 1; dl_sel = 0; dl_top = 0; return; }
    Level *L = &stack[depth];
    int n = L->n;
    if (gp_open) {
        if (pressed(K_O)) { gp_open = 0; return; }
        if (pressed(K_SQ) && n) {
            Node *g = &L->items[L->sel];
            trailer_open(g, has_arabic(g->title) && L->title ? L->title : g->title);
            return;
        }
        if (pressed(K_X)) { open_item(L); return; }
        int step = 0;
        if (pressed(K_LEFT) || pressed(K_R1)) step = 1;
        if (pressed(K_RIGHT) || pressed(K_L1)) step = -1;
        for (int i = L->sel + step; step && i >= 0 && i < n; i += step) if (is_game(&L->items[i])) { L->sel = i; break; }
        return;
    }
    if (n) {
        int old = L->sel;
        if (depth == 0 && home_row == 1) {
            /* inside the preview row of the focused section */
            int ready = pv_for == L->sel && pv.n;
            if (!ready || pressed(K_UP) || pressed(K_O)) { home_row = 0; return; }
            if (pressed(K_LEFT)) { if (pv_sel + 1 < pv.n) pv_sel++; }
            if (pressed(K_RIGHT)) { if (pv_sel > 0) pv_sel--; }
            if (pressed(K_R1)) { pv_sel += 5; if (pv_sel >= pv.n) pv_sel = pv.n - 1; }
            if (pressed(K_L1)) { pv_sel -= 5; if (pv_sel < 0) pv_sel = 0; }
            if (pressed(K_X)) {
                int want = pv_sel, d = depth;
                activate(&L->items[L->sel]);              /* opens the section */
                if (depth == d + 1 && want < stack[depth].n) { stack[depth].sel = want; gp_open = 0; open_item(&stack[depth]); }
                home_row = 0;
            }
            return;
        }
        if (depth == 0) {
            if (pressed(K_LEFT)) L->sel = (L->sel + 1) % n;
            if (pressed(K_RIGHT)) L->sel = (L->sel + n - 1) % n;
            if (pressed(K_R1)) { L->sel += 5; if (L->sel >= n) L->sel = n - 1; }
            if (pressed(K_L1)) { L->sel -= 5; if (L->sel < 0) L->sel = 0; }
            if (pressed(K_DOWN) && pv_for == L->sel && pv.n) { home_row = 1; pv_sel = 0; pv_first = 0; return; }
        } else {
            if (pressed(K_LEFT)) { if (L->sel + 1 < n) L->sel++; }
            if (pressed(K_RIGHT)) { if (L->sel > 0) L->sel--; }
            if (pressed(K_DOWN)) { if (L->sel + GCOLS < n) L->sel += GCOLS; else if (L->sel / GCOLS < (n - 1) / GCOLS) L->sel = n - 1; }
            if (pressed(K_UP)) { if (L->sel - GCOLS >= 0) L->sel -= GCOLS; }
            if (pressed(K_R1)) { L->sel += GCOLS * GROWS; if (L->sel >= n) L->sel = n - 1; }
            if (pressed(K_L1)) { L->sel -= GCOLS * GROWS; if (L->sel < 0) L->sel = 0; }
        }
        if (old != L->sel) pv_rest = 0;
        if (pressed(K_X)) { int d = depth; open_item(L); if (depth != d) gp_open = 0; }
    }
    if (pressed(K_O)) {
        if (depth > 0) { free_level(&stack[depth]); depth--; }
        else if (jobs_active() && !exit_warn) { exit_warn = 180; show_toast(S_EXITWARN); }
        else running = 0;
    }
}

/* the store root holds a single "ARCADIA" folder: start inside it */
static void enter_single_root(void) {
    if (depth != 0 || stack[0].n != 1 || !stack[0].items[0].folder) return;
    Node *n = &stack[0].items[0];
    Level tmp;
    if (!load_level(&tmp, n->tfile, n->tview)) { free_level(&tmp); return; }
    char *pic = stack[0].pic; stack[0].pic = NULL;
    free_level(&stack[0]);
    stack[0] = tmp; stack[0].pic = pic;
}

int main(int argc, const char *argv[]) {
    tiny3d_Init(1024 * 1024);
    ioPadInit(7);
    sysModuleLoad(SYSMODULE_PNGDEC);
    sysModuleLoad(SYSMODULE_JPGDEC);
#ifndef HOST_PREVIEW
    sysModuleLoad(SYSMODULE_VDEC); sysModuleLoad(SYSMODULE_VDEC_H264); audioInit();
#endif
    netInitialize();
    curl_global_init(CURL_GLOBAL_ALL);
    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, sys_cb, NULL);

    tex_size = 56 * 1024 * 1024;
    tex_pool = tiny3d_AllocTexture(tex_size); tex_used = 0;
    tex_make(&atlas, ATLAS, ATLAS_H);
    if (atlas.mem) memset(atlas.mem, 0, ATLAS * ATLAS_H * 4);
    tex_make(&t_circle, 64, 64); gen_circle(&t_circle, 0);
    tex_make(&t_ring, 64, 64); gen_circle(&t_ring, 4.5f);
    for (int i = 0; i < ICON_SLOTS; i++) tex_make(&icons[i].t, 320, 176);
    for (int i = 0; i < BG_SLOTS; i++) tex_make(&bgs[i].t, 960, 540);

    font_ok = !FT_Init_FreeType(&ft) && !FT_New_Face(ft, APPDIR "USRDIR/font.ttf", 0, &face) && !FT_Set_Pixel_Sizes(face, 0, GLYPH_PX) && atlas.mem;
    if (font_ok) preload_glyphs();

    jobs_load();
    video_init();
#ifndef HOST_PREVIEW
    worker_running = 1;
    if (sysThreadCreate(&worker_thread, worker_entry, NULL, 1200, 0x40000, 0, "arcadia_dl") != 0) worker_running = 0;
#endif
    if (load_level(&stack[0], ROOT_XML, ROOT_VIEW)) { depth = 0; stack[0].pic = strdup(STORE "PIC1.PNG"); enter_single_root(); }
    else free_level(&stack[0]);

    while (running) {
        frame_no++;
        sysUtilCheckCallback();
        webman_poll();
        dl_poll();
        read_pad();
        handle_input();
#ifdef HOST_PREVIEW
        { const char *e = getenv("FAKEDL"); if (e && frame_no == (u32)atoi(e)) {
            const char *ic = "/dev_hdd0/game/VIDESTORE/USRDIR/PS3/IMAGENES/PSNSTUFF/";
            const char *t[5] = {"1942: Joint Strike","4 Elements HD","Absolute Supercars","ACE COMBAT INFINITY","Alien Breed"};
            const char *f[5] = {"1942 - Joint Strike.png","4 Elements HD.png","Absolute Supercars.png","Ace Combat Infinity.png","Alien Breed.png"};
            int st[5] = {J_DOWNLOADING, J_QUEUED, J_DONE, J_FAILED, J_DONE};
            for (int i = 0; i < 5; i++) { Job *j = &jobs[i]; memset(j, 0, sizeof(*j)); j->used = 1; j->state = st[i]; j->notified = 1;
                snprintf(j->title, 256, "%s", t[i]); snprintf(j->info, 160, "NPUB3%04d - %d MB", 24 + i * 100, 130 + i * 300);
                snprintf(j->icon, 384, "%s%s", ic, f[i]); snprintf(j->url, 1024, "http://x/%d.pkg", i); }
            jobs[0].now = 84.5e6; jobs[0].total = 136.3e6; jobs[0].speed = 5.0e6; current_job = &jobs[0];
            snprintf(jobs[3].err, 160, "HTTP 404"); } }
#endif
        tiny3d_Clear(0xFF000000, TINY3D_CLEAR_ALL);
        tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
        tiny3d_BlendFunc(1, TINY3D_BLEND_FUNC_SRC_RGB_SRC_ALPHA | TINY3D_BLEND_FUNC_SRC_ALPHA_SRC_ALPHA,
                         TINY3D_BLEND_FUNC_DST_RGB_ONE_MINUS_SRC_ALPHA | TINY3D_BLEND_FUNC_DST_ALPHA_ZERO,
                         TINY3D_BLEND_RGB_FUNC_ADD | TINY3D_BLEND_ALPHA_FUNC_ADD);
        tiny3d_Project2D();
        draw_screen();
        tiny3d_Flip();
    }

    if (pl.active) player_close();
    tr_cancel = 1;
    worker_quit = 1; vf_quit = 1;
    for (int i = 0; (wm_busy || worker_running || vf_running) && i < 300; i++) usleep(20000);
    jobs_save();
    curl_global_cleanup();
    if (pv_for >= 0) free_level(&pv);
    while (depth >= 0) { free_level(&stack[depth]); depth--; }
    if (font_ok) { FT_Done_Face(face); FT_Done_FreeType(ft); }
    netDeinitialize();
    sysModuleUnload(SYSMODULE_PNGDEC);
    sysModuleUnload(SYSMODULE_JPGDEC);
    ioPadEnd();
    tiny3d_Exit();
    return 0;
}
