#pragma once
enum { RAP_DB_MISSING=-2, RAP_ERROR=-1, RAP_NOT_FOUND=0, RAP_INSTALLED=1, RAP_ALREADY_PRESENT=2 };
int rap_install_pkg(const char *, const char *, const char *, char *, int);
