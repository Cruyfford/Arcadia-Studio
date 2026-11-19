#pragma once
/* returns 1 on success; dest_root is "/dev_hdd0" on the console */
int pkg_install(const char *path, const char *dest_root, char *dest_out, int dest_len,
                char *err, int errlen, volatile double *done, volatile double *total);
