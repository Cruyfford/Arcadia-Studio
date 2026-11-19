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
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <net/net.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>
#include <sysmodule/sysmodule.h>
#include <pngdec/pngdec.h>
#include <tiny3d.h>
#include <ft2build.h>
#include FT_FREETYPE_H

#include "ui_strings.h"

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
    static const u32 ranges[][2] = { {0x20, 0x7E}, {0xA0, 0xFF}, {0x2010, 0x2027}, {0x2190, 0x2193},
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
static void show_toast(const char *s) { snprintf(toast, sizeof(toast), "%s", s); toast_frames = 60 * 5; }

static int webman_get(const char *path) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct sockaddr_in a; memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET; a.sin_port = htons(80); a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&a, sizeof(a)) < 0) { close(s); return 0; }
    static char req[3072]; char enc[2048]; int j = 0;
    for (const char *c = path; *c && j < (int)sizeof(enc) - 4; c++) {
        if (*c == ' ') { enc[j++] = '%'; enc[j++] = '2'; enc[j++] = '0'; } else enc[j++] = *c;
    }
    enc[j] = 0;
    int n = snprintf(req, sizeof(req), "GET %s HTTP/1.0\r\nHost: 127.0.0.1\r\nUser-Agent: ArcadiaPS3\r\n\r\n", enc);
    send(s, req, n, 0);
    char buf[256]; recv(s, buf, sizeof(buf), 0);
    close(s);
    return 1;
}

static void activate(Node *n) {
    if (n->folder) {
        if (depth + 1 >= MAX_DEPTH) return;
        Level *L = &stack[depth + 1];
        if (load_level(L, n->tfile, n->tview)) {
            L->title = strdup(n->title); L->pic = n->pic ? strdup(n->pic) : (stack[depth].pic ? strdup(stack[depth].pic) : NULL);
            depth++;
        } else {
            free_level(L); show_toast(S_EMPTY);
        }
        return;
    }
    if (n->action && !strncmp(n->action, "http://127.0.0.1", 16) && !strstr(n->action, "download.ps3")) {
        show_toast(webman_get(n->action + 16) ? S_CMD : S_NOWM); return;     /* store commands (font, themes...) */
    }
    if (!n->link) { show_toast(S_NOLINK); return; }
    char path[2048]; snprintf(path, sizeof(path), "/download.ps3?to=/dev_hdd0/packages&url=%s", n->link);
    show_toast(webman_get(path) ? S_SENT : S_NOWM);
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
#define ROWS 7
static void draw_screen(void) {
    Level *L = depth >= 0 ? &stack[depth] : NULL;
    Node *cur = (L && L->n) ? &L->items[L->sel] : NULL;
    /* background */
    Tex *bg = NULL;
    if (cur && cur->pic) bg = get_image(bgs, BG_SLOTS, cur->pic);
    if (!bg && L && L->pic) bg = get_image(bgs, BG_SLOTS, L->pic);
    if (bg) draw_image_fill(bg, 0, 0, SW, SH, 0xFFFFFFFF);
    else draw_grad(0, 0, SW, SH, 0x0B1A4AFF, 0x3A1060FF);
    draw_grad(0, 0, SW, SH, 0x00000040, 0x000000B0);

    /* header */
    draw_grad(0, 0, SW, 52, 0x000000C0, 0x00000000);
    draw_text_shadow("ARCADIA", 24, 34, 22, 0xFFFFFFFF, 0);
    if (L && L->title) draw_text_shadow(L->title, 150, 34, 18, 0xD8CCFFFF, 520);
    if (!L) { draw_text_shadow(S_NOSTORE, 40, 260, 22, 0xFFFFFFFF, SW - 80); return; }
    if (!L->n) { draw_text_shadow(S_EMPTY, 40, 260, 20, 0xFFFFFFFF, 0); }

    /* list */
    if (L->sel < L->top) L->top = L->sel;
    if (L->sel >= L->top + ROWS) L->top = L->sel - ROWS + 1;
    float y = 70, rh = 54;
    for (int i = L->top; i < L->n && i < L->top + ROWS; i++, y += rh) {
        Node *n = &L->items[i]; int s = i == L->sel;
        if (s) { draw_quad(20, y - 2, 500, rh - 4, 0x7C3AEDB0); draw_quad(20, y - 2, 4, rh - 4, 0xFFFFFFFF); }
        Tex *ic = n->icon ? get_image(icons, ICON_SLOTS, n->icon) : NULL;
        if (ic) draw_image_fit(ic, 30, y + 2, 80, rh - 12, 0xFFFFFFFF);
        else draw_quad(30, y + 8, 80, rh - 24, 0xFFFFFF22);
        draw_text_shadow(n->title, 122, y + (n->info ? 21 : 30), s ? 17 : 15, s ? 0xFFFFFFFF : 0xE6E6F0FF, 390);
        if (n->info) draw_text(n->info, 122, y + 40, 11.5f, 0xC9D3FFFF, 390);
        if (n->folder) draw_text(">", 500, y + 30, 16, 0xFFFFFF99, 0);
    }
    /* scroll bar */
    if (L->n > ROWS) {
        float th = ROWS * rh, bh = th * ROWS / L->n, by = 70 + (th - bh) * L->top / (L->n - ROWS);
        draw_quad(526, 68, 3, th, 0xFFFFFF22); draw_quad(526, by, 3, bh, 0xFFFFFFAA);
    }
    /* details of the selected item */
    if (cur) {
        draw_quad(548, 70, 280, 330, 0x0D1120B8);
        Tex *ic = cur->icon ? get_image(icons, ICON_SLOTS, cur->icon) : NULL;
        if (ic) draw_image_fit(ic, 560, 82, 256, 144, 0xFFFFFFFF);
        draw_text_shadow(cur->title, 560, 252, 15, 0xFFFFFFFF, 256);
        if (cur->info) draw_text(cur->info, 560, 276, 12, 0xC9D3FFFF, 256);
        draw_text(cur->folder ? S_OPEN : (cur->link || cur->action ? S_DL : S_NOLINK), 560, 380, 14, 0xFFD166FF, 256);
        char cnt[32]; snprintf(cnt, sizeof(cnt), "%d / %d", L->sel + 1, L->n);
        draw_text(cnt, 760, 380, 12, 0xFFFFFF99, 0);
    }
    /* footer */
    draw_grad(0, SH - 40, SW, 40, 0x00000000, 0x000000D0);
    draw_text(S_HINT, 24, SH - 14, 13, 0xFFFFFFCC, 0);
    if (toast_frames > 0) {
        toast_frames--;
        float w = text_width(toast, 14) + 30;
        if (w > SW - 40) w = SW - 40;
        draw_quad((SW - w) / 2, SH - 92, w, 34, 0x1A2150F0);
        draw_text(toast, (SW - w) / 2 + 15, SH - 70, 14, 0xFFFFFFFF, w - 30);
    }
}

int main(int argc, const char *argv[]) {
    tiny3d_Init(1024 * 1024);
    ioPadInit(7);
    sysModuleLoad(SYSMODULE_PNGDEC);
    netInitialize();
    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, sys_cb, NULL);

    tex_size = 40 * 1024 * 1024;
    tex_pool = tiny3d_AllocTexture(tex_size); tex_used = 0;
    tex_make(&atlas, ATLAS, ATLAS_H);
    if (atlas.mem) memset(atlas.mem, 0, ATLAS * ATLAS_H * 4);
    for (int i = 0; i < ICON_SLOTS; i++) tex_make(&icons[i].t, 320, 176);
    for (int i = 0; i < BG_SLOTS; i++) tex_make(&bgs[i].t, 960, 540);

    font_ok = !FT_Init_FreeType(&ft) && !FT_New_Face(ft, APPDIR "USRDIR/font.ttf", 0, &face) && !FT_Set_Pixel_Sizes(face, 0, GLYPH_PX) && atlas.mem;
    if (font_ok) preload_glyphs();

    if (load_level(&stack[0], ROOT_XML, ROOT_VIEW)) { depth = 0; stack[0].pic = strdup(STORE "PIC1.PNG"); }
    else free_level(&stack[0]);

    while (running) {
        frame_no++;
        sysUtilCheckCallback();
        read_pad();
        if (depth >= 0) {
            Level *L = &stack[depth];
            if (L->n) {
                if (pressed(K_DOWN)) L->sel = (L->sel + 1) % L->n;
                if (pressed(K_UP)) L->sel = (L->sel + L->n - 1) % L->n;
                if (pressed(K_R1) || pressed(K_RIGHT)) { L->sel += ROWS; if (L->sel >= L->n) L->sel = L->n - 1; }
                if (pressed(K_L1) || pressed(K_LEFT)) { L->sel -= ROWS; if (L->sel < 0) L->sel = 0; }
                if (pressed(K_X)) activate(&L->items[L->sel]);
            }
            if (pressed(K_O)) {
                if (depth > 0) { free_level(&stack[depth]); depth--; }
                else running = 0;
            }
        } else if (pressed(K_O) || pressed(K_X)) running = 0;
        if (pressed(K_START)) running = 0;

        tiny3d_Clear(0xFF000000, TINY3D_CLEAR_ALL);
        tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
        tiny3d_BlendFunc(1, TINY3D_BLEND_FUNC_SRC_RGB_SRC_ALPHA | TINY3D_BLEND_FUNC_SRC_ALPHA_SRC_ALPHA,
                         TINY3D_BLEND_FUNC_DST_RGB_ONE_MINUS_SRC_ALPHA | TINY3D_BLEND_FUNC_DST_ALPHA_ZERO,
                         TINY3D_BLEND_RGB_FUNC_ADD | TINY3D_BLEND_ALPHA_FUNC_ADD);
        tiny3d_Project2D();
        draw_screen();
        tiny3d_Flip();
    }

    while (depth >= 0) { free_level(&stack[depth]); depth--; }
    if (font_ok) { FT_Done_Face(face); FT_Done_FreeType(ft); }
    netDeinitialize();
    sysModuleUnload(SYSMODULE_PNGDEC);
    ioPadEnd();
    tiny3d_Exit();
    return 0;
}
