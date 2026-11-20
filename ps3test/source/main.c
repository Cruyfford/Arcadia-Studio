/* Arcadia hardware test: a coloured screen with a moving square.
   TEST_LIBS=1 also links the same system libraries as the Arcadia app. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <tiny3d.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>
#if TEST_LIBS
#include <sysmodule/sysmodule.h>
#include <net/net.h>
#include <curl/curl.h>
#include <codec/vdec.h>
#include <audio/audio.h>
#include <jpgdec/jpgdec.h>
#include <pngdec/pngdec.h>
#include <ft2build.h>
#include FT_FREETYPE_H
volatile void *keep[] = { (void *)vdecOpen, (void *)audioInit, (void *)curl_easy_init, (void *)jpgLoadFromBuffer,
                          (void *)pngLoadFromBuffer, (void *)FT_Init_FreeType, (void *)netInitialize };
#endif
static int running = 1;
static void cb(u64 status, u64 param, void *ud) { if (status == SYSUTIL_EXIT_GAME) running = 0; }
static void quad(float x, float y, float w, float h, u32 c) {
    tiny3d_SetPolygon(TINY3D_QUADS);
    tiny3d_VertexPos(x, y, 65535); tiny3d_VertexColor(c);
    tiny3d_VertexPos(x + w, y, 65535); tiny3d_VertexColor(c);
    tiny3d_VertexPos(x + w, y + h, 65535); tiny3d_VertexColor(c);
    tiny3d_VertexPos(x, y + h, 65535); tiny3d_VertexColor(c);
    tiny3d_End();
}
int main(int argc, const char *argv[]) {
    FILE *f = fopen("/dev_hdd0/tmp/arcadia_test.txt", "w"); if (f) { fprintf(f, "test %d start\n", TEST_LIBS); fclose(f); }
    tiny3d_Init(1024 * 1024);
    ioPadInit(7);
    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, cb, NULL);
#if TEST_LIBS
    sysModuleLoad(SYSMODULE_PNGDEC); sysModuleLoad(SYSMODULE_JPGDEC); netInitialize(); curl_global_init(CURL_GLOBAL_ALL);
#endif
    float x = 100, dx = 4; u32 bg = TEST_LIBS ? 0xFF1030A0 : 0xFF108030;
    while (running) {
        sysUtilCheckCallback();
        padInfo pi; padData pd; ioPadGetInfo(&pi);
        for (int i = 0; i < MAX_PADS; i++) if (pi.status[i]) { ioPadGetData(i, &pd); if (pd.BTN_CIRCLE) running = 0; }
        tiny3d_Clear(bg, TINY3D_CLEAR_ALL);
        tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
        tiny3d_Project2D();
        x += dx; if (x < 40 || x > 760) dx = -dx;
        quad(x, 220, 60, 60, 0xFFFFFFFF);
        quad(40, 400, 768, 12, TEST_LIBS ? 0x40A0FFFF : 0x60FF80FF);
        tiny3d_Flip();
    }
    ioPadEnd(); tiny3d_Exit();
    return 0;
}
