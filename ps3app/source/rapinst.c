
#include "rapinst.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

static unsigned be32(const unsigned char *p) {
    return ((unsigned)p[0]<<24)|((unsigned)p[1]<<16)|((unsigned)p[2]<<8)|p[3];
}
static int valid_id(const char *s) {
    if(!s||strlen(s)!=36||s[6]!='-'||s[16]!='_'||s[17]!='0'||s[18]!='0'||s[19]!='-')return 0;
    for(int i=0;i<36;++i){
        if(i==6||i==16||i==17||i==18||i==19)continue;
        char c=s[i];
        if(!((c>='A'&&c<='Z')||(c>='0'&&c<='9')||(i>=20&&c=='_')))return 0;
    }
    return 1;
}
static int from_pkg(const char *pkg,char cid[37]){
    unsigned char h[0x60];
    FILE *f=fopen(pkg,"rb");
    if(!f)return 0;
    size_t n=fread(h,1,sizeof(h),f);
    fclose(f);
    if(n!=sizeof(h)||memcmp(h,"\177PKG",4)||h[6]!=0||h[7]!=1)return 0;
    memcpy(cid,h+0x30,36);cid[36]=0;
    return valid_id(cid);
}
static int find_rap(const char *path,const char *cid,unsigned char rap[16]){
    FILE *f=fopen(path,"rb");
    if(!f)return RAP_DB_MISSING;
    unsigned char h[12],rec[52];
    int result=RAP_ERROR;
    if(fread(h,1,12,f)!=12||memcmp(h,"ARCRAP01",8))goto end;
    unsigned count=be32(h+8);
    if(!count||count>500000)goto end;
    if(fseek(f,0,SEEK_END))goto end;
    long sz=ftell(f);
    if(sz!=(long)(12+count*52))goto end;
    int l=0,r=(int)count-1;
    while(l<=r){
        int mid=l+(r-l)/2;
        if(fseek(f,12L+(long)mid*52,SEEK_SET)||fread(rec,1,52,f)!=52)goto end;
        int cmp=memcmp(cid,rec,36);
        if(cmp==0){memcpy(rap,rec+36,16);result=RAP_INSTALLED;goto end;}
        if(cmp<0)r=mid-1;else l=mid+1;
    }
    result=RAP_NOT_FOUND;
end:
    fclose(f);return result;
}
static int copy_rap(const char *cid,const char *dest,const unsigned char rap[16]){
    if(mkdir(dest,0777)!=0&&errno!=EEXIST)return RAP_ERROR;
    char out[512],tmp[512];
    if(snprintf(out,sizeof(out),"%s/%s.rap",dest,cid)>=(int)sizeof(out))return RAP_ERROR;
    if(snprintf(tmp,sizeof(tmp),"%s/%s.rap.tmp",dest,cid)>=(int)sizeof(tmp))return RAP_ERROR;
    struct stat st;
    if(stat(out,&st)==0){
        unsigned char prev[16];
        FILE *f=fopen(out,"rb");if(!f)return RAP_ERROR;
        size_t n=fread(prev,1,16,f);fclose(f);
        return st.st_size==16&&n==16&&!memcmp(prev,rap,16)?RAP_ALREADY_PRESENT:RAP_ERROR;
    }
    if(errno!=ENOENT)return RAP_ERROR;
    FILE *f=fopen(tmp,"wb");if(!f)return RAP_ERROR;
    size_t n=fwrite(rap,1,16,f);
    int e=fclose(f);
    if(n!=16||e||rename(tmp,out)){remove(tmp);return RAP_ERROR;}
    return RAP_INSTALLED;
}
int rap_install_pkg(const char *pkg,const char *db,const char *exdata,char *msg,int msglen){
    char cid[37];
    if(!from_pkg(pkg,cid)){if(msg&&msglen)snprintf(msg,msglen,"Invalid PKG Content ID");return RAP_ERROR;}
    unsigned char rap[16];
    int result=find_rap(db,cid,rap);
    if(result==RAP_DB_MISSING)result=find_rap("/dev_usb000/rapdb.bin",cid,rap);
    if(result==RAP_DB_MISSING)result=find_rap("/dev_usb001/rapdb.bin",cid,rap);
    if(result==RAP_DB_MISSING)result=find_rap("/dev_hdd0/rapdb.bin",cid,rap);
    if(result==RAP_INSTALLED) {
      result=copy_rap(cid,exdata,rap);
    }
    if(msg&&msglen)snprintf(msg,msglen,"RAP status %d",result);
    return result;
}
