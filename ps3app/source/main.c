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
#include <tiny3d.h>
#include <sys/stat.h>
#include <curl/curl.h>
#include <ft2build.h>
#include FT_FREETYPE_H

#include "ui_strings.h"
#include "pkginst.h"

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
    pngData png; memset(&png, 0, sizeof(png));
    if (pngLoadFromFile(path, &png) != 0 || !png.bmp_out) return 0;
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

/* ------------------------------------------------------------------ built-in downloader (no webMAN needed) */
#define PKG_DIR "/dev_hdd0/packages"
static volatile int dl_active, dl_cancel, dl_state, dl_phase;   /* phase 0 download, 1 install */   /* state: 0 none, 1 done, 2 fail, 3 canceled */
static volatile double dl_now, dl_total, dl_speed;
static char dl_err[160]; static char dl_icon[512];
static char dl_url[2048], dl_name[256], dl_title[256], dl_final[512], dl_part[512];
static sys_ppu_thread_t dl_thread;

static size_t dl_write(void *p, size_t sz, size_t n, void *f) { return fwrite(p, sz, n, (FILE *)f); }

static int dl_progress(void *u, curl_off_t total, curl_off_t now, curl_off_t ut, curl_off_t un) {
    dl_total = (double)total; dl_now = (double)now;
    return dl_cancel ? 1 : 0;
}

static void dl_entry(void *arg) {
    int state = 2;
    FILE *f = fopen(dl_part, "wb");
    snprintf(dl_err, sizeof(dl_err), f ? "curl init" : "open " PKG_DIR " failed");
    CURL *c = f ? curl_easy_init() : NULL;
    if (c) {
        static char fbuf[1 << 20]; setvbuf(f, fbuf, _IOFBF, sizeof(fbuf));
        curl_easy_setopt(c, CURLOPT_URL, dl_url);
        curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(c, CURLOPT_MAXREDIRS, 10L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 0L);
        curl_easy_setopt(c, CURLOPT_USERAGENT, "Mozilla/5.0 (PLAYSTATION 3; 4.92)");
        curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, dl_write);
        curl_easy_setopt(c, CURLOPT_WRITEDATA, f);
        curl_easy_setopt(c, CURLOPT_NOPROGRESS, 0L);
        curl_easy_setopt(c, CURLOPT_XFERINFOFUNCTION, dl_progress);
        curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 20L);
        curl_easy_setopt(c, CURLOPT_LOW_SPEED_LIMIT, 1L);
        curl_easy_setopt(c, CURLOPT_LOW_SPEED_TIME, 60L);
        curl_easy_setopt(c, CURLOPT_BUFFERSIZE, 512L * 1024);
        CURLcode r = curl_easy_perform(c);
        long code = 0; curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(c);
        fclose(f); f = NULL;
        if (r == CURLE_OK && code < 400) {
            unlink(dl_final);
            if (rename(dl_part, dl_final) != 0) state = 2;
            else {
                /* install right here, inside the app */
                dl_phase = 1; dl_now = 0; dl_total = 0;
                char dest[512];
                if (pkg_install(dl_final, "/dev_hdd0", dest, sizeof(dest), dl_err, sizeof(dl_err), &dl_now, &dl_total)) { unlink(dl_final); state = 1; }
                else state = 5;
            }
        }
        else {
            state = dl_cancel ? 3 : (r == CURLE_WRITE_ERROR ? 4 : 2);
            if (r != CURLE_OK) snprintf(dl_err, sizeof(dl_err), "curl %d: %s", (int)r, curl_easy_strerror(r));
            else snprintf(dl_err, sizeof(dl_err), "HTTP %ld", code);
        }
    }
    if (f) fclose(f);
    if (state != 1) unlink(dl_part);
    dl_state = state; dl_active = 0;
    sysThreadExit(0);
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

static const char *cur_icon, *cur_info;
static char dl_info[256];
static void start_download(const char *url, const char *title) {
    if (dl_active) return;
    snprintf(dl_info, sizeof(dl_info), "%s", cur_info ? cur_info : "");
    dl_icon[0] = 0; if (cur_icon) snprintf(dl_icon, sizeof(dl_icon), "%s", cur_icon);
    snprintf(dl_url, sizeof(dl_url), "%s", url);
    for (char *c = dl_url; *c; c++) if (*c == ' ') { memmove(c + 3, c + 1, strlen(c + 1) + 1); c[0] = '%'; c[1] = '2'; c[2] = '0'; }
    url_filename(url, dl_name, sizeof(dl_name));
    snprintf(dl_title, sizeof(dl_title), "%s", title ? title : dl_name);
    mkdir(PKG_DIR, 0777);
    snprintf(dl_final, sizeof(dl_final), PKG_DIR "/%s", dl_name);
    snprintf(dl_part, sizeof(dl_part), PKG_DIR "/%s.part", dl_name);
    dl_now = dl_total = dl_speed = 0; dl_cancel = 0; dl_state = 0; dl_phase = 0; dl_active = 1;
    if (sysThreadCreate(&dl_thread, dl_entry, NULL, 1200, 0x40000, 0, "arcadia_dl") != 0) { dl_active = 0; show_toast(S_FAIL); }
}

static int ui_done;
static void dl_poll(void) {
    static double last_now; static u32 last_frame;
    if (dl_active && frame_no - last_frame >= 60) {
        double d = dl_now - last_now; dl_speed = d > 0 ? d * 60.0 / (frame_no - last_frame) : dl_speed * 0.5;
        last_now = dl_now; last_frame = frame_no;
    }
    if (!dl_active && dl_state) {
        if (dl_state == 2) { char t[300]; snprintf(t, sizeof(t), "(%s)  %s", dl_err, S_FAIL); show_toast(t); }
        else if (dl_state == 1) ui_done = 1;
        else if (dl_state == 5) { char t[300]; snprintf(t, sizeof(t), "(%s)  %s", dl_err, S_BADPKG); show_toast(t); }
        else show_toast(dl_state == 1 ? S_DONE : dl_state == 3 ? S_CANCELED : S_SPACE);
        dl_state = 0; last_now = 0;
    }
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
    if (count) text_r(count, LX + text_width(count, 10), 56, 10, C_MUTED, 0);
}

/* ------------------------------------------------------------------ home (top level): category tiles + preview */
static Level pv; static int pv_for = -1, pv_rest;
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
        if (f) { draw_rrect(x - w - 4, y - 4, w + 8, h + 8, 17, 0x7C3AED40); draw_rrect(x - w - 1.5f, y - 1.5f, w + 3, h + 3, 14, C_ACCENT); draw_rrect(x - w, y, w, h, 13, 0x2A1E5CFF); }
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
        for (int i = 0; i < pv.n && i < 5; i++) {
            Node *n = &pv.items[i];
            draw_rrect(cx - cw, 302, cw, 79, 8, 0x0000004D);
            Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
            if (t) draw_image_fit(t, cx - cw, 302, cw, 79, 0xFFFFFFFF);
            text_rb(n->title, cx, 400, 10, C_TEXT, cw);
            if (n->info) text_r(n->info, cx, 414, 8.6f, C_MUTED, cw);
            cx -= cw + 12;
        }
    } else if (pv_for != L->sel) text_r(S_LOADING, RX, 340, 10, C_MUTED, 0);
    else text_r(cur->info ? cur->info : S_EMPTY, RX, 340, 10, C_MUTED, 600);

    Hint h[] = { {0, S_OPENL}, {1, S_EXITL}, {2, S_NAV} };
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", L->sel + 1, L->n);
    draw_footer(h, 3, pos);
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
    Hint h[] = { {0, games ? S_DLINST : S_OPENL}, {1, S_BACK}, {2, S_PAGE} };
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", L->n ? L->sel + 1 : 0, L->n);
    draw_footer(h, 3, pos);
}

/* ------------------------------------------------------------------ game page */
static int gp_open;
static void draw_game(Level *L) {
    Node *n = &L->items[L->sel];
    draw_backdrop(n->pic ? n->pic : L->pic, 1);
    char c[600]; crumbs(depth, c, sizeof(c));
    text_r(c, RX, 38, 9.3f, C_MUTED, 700);

    /* cover */
    float cs = 238, cx = RX - cs, cy = 58;
    draw_rrect(cx, cy, cs, cs, 16, 0x00000073);
    Tex *t = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
    if (t) draw_image_fit(t, cx + 6, cy + 6, cs - 12, cs - 12, 0xFFFFFFFF);

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
    Hint h[] = { {0, S_DLINST}, {1, S_BACK}, {2, S_PREVNEXT} };
    char pos[32]; snprintf(pos, sizeof(pos), "%d / %d", L->sel + 1, L->n);
    draw_footer(h, 3, pos);
}

/* ------------------------------------------------------------------ download / install panel */
static void draw_progress(void) {
    if (!dl_active && !ui_done) return;
    draw_quad(0, 0, SW, SH, 0x04050CE0);
    float w = 477, h = 236, x = (SW - w) / 2, y = (SH - h) / 2 - 6, r = x + w - 26;
    draw_rrect(x, y, w, h, 18, 0x12152EFF);

    Tex *t = dl_icon[0] ? get_image(icons, ICON_SLOTS, dl_icon) : NULL;
    draw_rrect(r - 56, y + 24, 56, 56, 11, 0x00000080);
    if (t) draw_image_fit(t, r - 56, y + 24, 56, 56, 0xFFFFFFFF);
    float tr = r - 68;
    const char *st = ui_done ? S_DONE2 : dl_phase ? S_INSTALLING : S_DLING;
    text_r(st, tr, y + 38, 10, ui_done ? C_MINT : C_GOLD, 0);
    text_rb(dl_title, tr, y + 60, 18, C_TEXT, tr - x - 26);
    if (dl_info[0]) text_r(dl_info, tr, y + 75, 8.6f, C_MUTED, tr - x - 26);

    /* stepper */
    int at = ui_done ? 3 : dl_phase ? 1 : 0;
    const char *lbl[3] = { S_STEP1, S_STEP2, S_STEP3 };
    float sx = r, sy = y + 108, seg = (w - 52) / 3;
    for (int k = 0; k < 3; k++) {
        int done = k < at, act = k == at;
        float dcx = sx - 10.5f;
        if (done) draw_circle(dcx, sy, 10.5f, C_OKC);
        else if (act) { draw_circle(dcx, sy, 14, 0x7C3AED40); draw_circle(dcx, sy, 10.5f, C_ACCENT); }
        else draw_circle(dcx, sy, 10.5f, 0xFFFFFF14);
        if (done) glyph_check(dcx, sy, 11, 0xFFFFFFFF);
        else { char d[4]; snprintf(d, sizeof(d), "%d", k + 1); draw_text(d, dcx - text_width(d, 10) / 2, sy + 3.6f, 10, act ? 0xFFFFFFFF : C_MUTED, 0); }
        float lw = text_width(lbl[k], 10.5f);
        draw_text(lbl[k], sx - 27 - lw, sy + 3.8f, 10.5f, act || done ? 0xFFFFFFFF : C_MUTED, 0);
        if (k < 2) {
            float l0 = sx - 33 - lw, l1 = sx - seg + 4;
            draw_quad(l1, sy - 0.75f, l0 - l1, 1.5f, at > k ? C_OKC : 0xFFFFFF24);
        }
        sx -= seg;
    }

    if (!ui_done) {
        float pct = dl_total > 0 ? (float)(dl_now / dl_total) : 0;
        if (pct > 1) pct = 1;
        float bx = x + 26, bw = w - 52, by = y + 140;
        draw_rrect(bx, by, bw, 10, 5, 0xFFFFFF1A);
        if (pct > 0.01f) draw_rrect(bx + bw * (1 - pct), by, bw * pct, 10, 5, C_ACCENT);   /* fills from the right (RTL) */
        char p[16], d[96];
        snprintf(p, sizeof(p), "%d%%", (int)(pct * 100));
        if (dl_phase) snprintf(d, sizeof(d), "%.1f / %.1f MB", dl_now / 1048576.0, dl_total / 1048576.0);
        else if (dl_total > 0) snprintf(d, sizeof(d), "%.1f / %.1f MB   \xC2\xB7   %.2f MB/s", dl_now / 1048576.0, dl_total / 1048576.0, dl_speed / 1048576.0);
        else snprintf(d, sizeof(d), "%.1f MB   \xC2\xB7   %.2f MB/s", dl_now / 1048576.0, dl_speed / 1048576.0);
        draw_text(p, bx, by + 32, 14.5f, C_TEXT, 0); draw_text(p, bx + 0.5f, by + 32, 14.5f, C_TEXT, 0);
        draw_text(d, bx + bw - text_width(d, 10.5f), by + 30, 10.5f, 0xCFCBE6FF, 0);
    } else {
        float bx = x + 26, bw = w - 52, by = y + 132;
        draw_rrect(bx, by, bw, 44, 11, 0x2EA06729);
        glyph_check(bx + bw - 22, by + 22, 14, C_MINT);
        text_r(S_DONEMSG, bx + bw - 38, by + 26, 10.5f, C_TEXT, bw - 50);
    }

    /* panel footer */
    float fy = y + h - 26;
    glyph_o(r - 8, fy, 8);
    const char *ol = ui_done ? S_TOSTORE : dl_phase ? S_NOCANCEL : S_CANCELDL;
    draw_text(ol, r - 22 - text_width(ol, 10), fy + 3.6f, 10, C_SOFT, 0);
    const char *hint = ui_done ? S_PKGDEL : dl_phase ? S_KEEPON : S_TEMP;
    text_r(hint, x + 26 + text_width(hint, 8.6f), fy + 3.2f, 8.6f, 0x8F89B3FF, 0);
}

static void draw_screen(void) {
    if (depth < 0) {
        draw_backdrop(NULL, 1);
        text_c(S_NOSTORE, SW / 2, SH / 2, 14, C_TEXT, SW - 80);
        Hint h[] = { {1, S_EXITL} };
        draw_footer(h, 1, NULL);
        draw_toast();
        return;
    }
    Level *L = &stack[depth];
    if (gp_open && L->n) draw_game(L);
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
    if (dl_active) { if (pressed(K_O)) { if (dl_phase) show_toast(S_NOCANCEL); else dl_cancel = 1; } return; }
    if (ui_done) { if (pressed(K_O) || pressed(K_X)) ui_done = 0; return; }
    if (depth < 0) { if (pressed(K_O) || pressed(K_X)) running = 0; return; }
    Level *L = &stack[depth];
    int n = L->n;
    if (gp_open) {
        if (pressed(K_O)) { gp_open = 0; return; }
        if (pressed(K_X)) { open_item(L); return; }
        int step = 0;
        if (pressed(K_LEFT) || pressed(K_R1)) step = 1;
        if (pressed(K_RIGHT) || pressed(K_L1)) step = -1;
        for (int i = L->sel + step; step && i >= 0 && i < n; i += step) if (is_game(&L->items[i])) { L->sel = i; break; }
        return;
    }
    if (n) {
        int old = L->sel;
        if (depth == 0) {
            if (pressed(K_LEFT) || pressed(K_DOWN)) L->sel = (L->sel + 1) % n;
            if (pressed(K_RIGHT) || pressed(K_UP)) L->sel = (L->sel + n - 1) % n;
            if (pressed(K_R1)) { L->sel += 5; if (L->sel >= n) L->sel = n - 1; }
            if (pressed(K_L1)) { L->sel -= 5; if (L->sel < 0) L->sel = 0; }
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
    netInitialize();
    curl_global_init(CURL_GLOBAL_ALL);
    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, sys_cb, NULL);

    tex_size = 40 * 1024 * 1024;
    tex_pool = tiny3d_AllocTexture(tex_size); tex_used = 0;
    tex_make(&atlas, ATLAS, ATLAS_H);
    if (atlas.mem) memset(atlas.mem, 0, ATLAS * ATLAS_H * 4);
    tex_make(&t_circle, 64, 64); gen_circle(&t_circle, 0);
    tex_make(&t_ring, 64, 64); gen_circle(&t_ring, 4.5f);
    for (int i = 0; i < ICON_SLOTS; i++) tex_make(&icons[i].t, 320, 176);
    for (int i = 0; i < BG_SLOTS; i++) tex_make(&bgs[i].t, 960, 540);

    font_ok = !FT_Init_FreeType(&ft) && !FT_New_Face(ft, APPDIR "USRDIR/font.ttf", 0, &face) && !FT_Set_Pixel_Sizes(face, 0, GLYPH_PX) && atlas.mem;
    if (font_ok) preload_glyphs();

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
        { const char *e = getenv("FAKEDL"); if (e && frame_no == (u32)atoi(e)) { int ph = atoi(getenv("FAKEPH"));
            snprintf(dl_title, sizeof(dl_title), "1942: Joint Strike"); snprintf(dl_info, sizeof(dl_info), "NPUB30024 - 130 MB");
            snprintf(dl_icon, sizeof(dl_icon), "/dev_hdd0/game/VIDESTORE/USRDIR/PS3/IMAGENES/PSNSTUFF/1942 - Joint Strike.png");
            if (ph == 2) ui_done = 1; else { dl_active = 1; dl_phase = ph; dl_now = ph ? 47.7e6 : 84.5e6; dl_total = 136.3e6; dl_speed = 5.0e6; } } }
#endif
        if (!dl_active && pressed(K_START)) running = 0;

        tiny3d_Clear(0xFF000000, TINY3D_CLEAR_ALL);
        tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
        tiny3d_BlendFunc(1, TINY3D_BLEND_FUNC_SRC_RGB_SRC_ALPHA | TINY3D_BLEND_FUNC_SRC_ALPHA_SRC_ALPHA,
                         TINY3D_BLEND_FUNC_DST_RGB_ONE_MINUS_SRC_ALPHA | TINY3D_BLEND_FUNC_DST_ALPHA_ZERO,
                         TINY3D_BLEND_RGB_FUNC_ADD | TINY3D_BLEND_ALPHA_FUNC_ADD);
        tiny3d_Project2D();
        draw_screen();
        draw_progress();
        tiny3d_Flip();
    }

    dl_cancel = 1;
    for (int i = 0; (wm_busy || dl_active) && i < 300; i++) usleep(20000);
    curl_global_cleanup();
    if (pv_for >= 0) free_level(&pv);
    while (depth >= 0) { free_level(&stack[depth]); depth--; }
    if (font_ok) { FT_Done_Face(face); FT_Done_FreeType(ft); }
    netDeinitialize();
    sysModuleUnload(SYSMODULE_PNGDEC);
    ioPadEnd();
    tiny3d_Exit();
    return 0;
}
