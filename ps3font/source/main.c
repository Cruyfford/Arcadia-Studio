/* ARCADIA Arabic Font: installs the Arabic XMB font without webMAN.
   Mounts /dev_blind itself, backs up the original font to the HDD, copies, verifies, unmounts. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <ppu-lv2.h>
#include <tiny3d.h>
#include <sysutil/sysutil.h>
#include <sysutil/msg.h>

#define APPDIR  "/dev_hdd0/game/ARCF00001/USRDIR/"
#define SRC     APPDIR "arabic.ttf"
#define BACKUP  APPDIR "original.ttf"
#define TARGET  "/dev_blind/data/font/SCE-PS3-RD-R-LATIN2.TTF"
#define LOG     "/dev_hdd0/tmp/arcadia_font.txt"

static int running = 1;
static volatile int dlg_open; static volatile int dlg_ans;

static void sys_cb(u64 status, u64 param, void *ud) { if (status == SYSUTIL_EXIT_GAME) running = 0; }
static void dlg_cb(msgButton b, void *ud) { dlg_ans = b; dlg_open = 0; }

static void frame(void) {
    tiny3d_Clear(0xFF120A2E, TINY3D_CLEAR_ALL);
    tiny3d_AlphaTest(1, 0x01, TINY3D_ALPHA_FUNC_GEQUAL);
    tiny3d_Project2D();
    tiny3d_Flip();
}

/* returns 1 = yes/ok */
static int dialog(const char *msg, int yesno) {
    dlg_open = 1; dlg_ans = MSG_DIALOG_BTN_NONE;
    msgType t = MSG_DIALOG_NORMAL | MSG_DIALOG_DISABLE_CANCEL_ON | (yesno ? MSG_DIALOG_BTN_TYPE_YESNO : MSG_DIALOG_BTN_TYPE_OK);
    if (msgDialogOpen2(t, msg, dlg_cb, NULL, NULL) != 0) return 0;
    while (dlg_open && running) { sysUtilCheckCallback(); frame(); }
    return dlg_ans == MSG_DIALOG_BTN_YES;
}

static void logf_(const char *m, int v) { FILE *f = fopen(LOG, "a"); if (f) { fprintf(f, "%s %d (0x%08X)\n", m, v, (unsigned)v); fclose(f); } }

static int sys_mount(const char *dev, const char *fs, const char *path) {
    lv2syscall8(837, (u64)dev, (u64)fs, (u64)path, 0, 0, 0, 0, 0);
    return_to_user_prog(int);
}
static int sys_umount(const char *path) {
    lv2syscall1(838, (u64)path);
    return_to_user_prog(int);
}

static long fsize(const char *p) { struct stat st; return stat(p, &st) == 0 ? (long)st.st_size : -1; }

static int copy_file(const char *a, const char *b) {
    FILE *i = fopen(a, "rb"); if (!i) return -1;
    FILE *o = fopen(b, "wb"); if (!o) { fclose(i); return -2; }
    char buf[16384]; size_t n; int ok = 0;
    while ((n = fread(buf, 1, sizeof(buf), i)) > 0) if (fwrite(buf, 1, n, o) != n) { ok = -3; break; }
    fclose(i); if (fclose(o) != 0 && !ok) ok = -4;
    return ok;
}

static int same_file(const char *a, const char *b) {
    FILE *x = fopen(a, "rb"), *y = fopen(b, "rb"); int same = x && y;
    char p[8192], q[8192];
    while (same) { size_t n = fread(p, 1, sizeof(p), x), m = fread(q, 1, sizeof(q), y); if (n != m || memcmp(p, q, n)) same = 0; if (!n) break; }
    if (x) fclose(x); if (y) fclose(y);
    return same;
}

int main(int argc, const char *argv[]) {
    tiny3d_Init(1024 * 1024);
    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, sys_cb, NULL);
    { FILE *f = fopen(LOG, "w"); if (f) fclose(f); }
    frame();

    int install = dialog("ARCADIA - Arabic font\n\nInstall the Arabic font for the XMB?\n(The original font is kept as a backup.)\n\nYES = install     NO = more options", 1);
    int restore = 0;
    if (!install && running) restore = dialog("Restore the original PS3 font?", 1);
    if (!install && !restore) goto out;

    const char *from = install ? SRC : BACKUP;
    if (fsize(from) < 10000) { dialog(install ? "Error: the Arabic font file is missing from the app." : "No backup found - nothing to restore.", 0); goto out; }

    int mounted_here = 0;
    if (fsize(TARGET) <= 0) {
        int r = sys_mount("CELL_FS_IOS:BUILTIN_FLSH1", "CELL_FS_FAT", "/dev_blind");
        logf_("mount", r);
        mounted_here = (r == 0);
    }
    long tsz = fsize(TARGET);
    logf_("target size", (int)tsz);
    if (tsz <= 0) { dialog("Error: could not open /dev_blind (system flash).\nMake sure HEN is enabled, then try again.", 0); goto out; }

    if (install && fsize(BACKUP) <= 0) {
        int r = copy_file(TARGET, BACKUP); logf_("backup", r);
        if (r || !same_file(TARGET, BACKUP)) { unlink(BACKUP); if (mounted_here) sys_umount("/dev_blind"); dialog("Error: could not back up the original font. Nothing was changed.", 0); goto out; }
    }

    int r = copy_file(from, TARGET); logf_("copy", r);
    int ok = (r == 0) && same_file(from, TARGET);
    if (!ok && fsize(BACKUP) > 0) { int r2 = copy_file(BACKUP, TARGET); logf_("rollback", r2); }

    if (mounted_here) logf_("umount", sys_umount("/dev_blind"));

    if (ok) dialog(install ? "Arabic font installed.\n\nRestart the PS3 (from the XMB, not the power button)." : "Original font restored.\n\nRestart the PS3.", 0);
    else dialog("Error: the font could not be written. The original font was put back.", 0);
out:
    tiny3d_Exit();
    return 0;
}
