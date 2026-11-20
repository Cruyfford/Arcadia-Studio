#pragma once
/* Minimal MP4 (ISO BMFF) demuxer: one H.264 video track + one AAC audio track, enough for game trailers. */
#include <stdint.h>
#include <stdio.h>

typedef struct {
    uint64_t off;     /* byte offset in the file */
    uint32_t size;
    int64_t dts;      /* in track timescale */
    int32_t cts;      /* composition offset */
    uint8_t key;
} Mp4Sample;

typedef struct {
    int present;
    uint32_t timescale;
    int64_t duration;
    uint32_t count;
    Mp4Sample *s;
    /* video */
    int width, height, nal_len;          /* nal_len: bytes of the NAL length prefix (avcC) */
    uint8_t *sps_pps; int sps_pps_len;   /* Annex-B: 00 00 00 01 SPS 00 00 00 01 PPS ... */
    int profile, level;
    /* audio */
    int channels, rate;
    uint8_t asc[64]; int asc_len;        /* AudioSpecificConfig from esds */
} Mp4Track;

typedef struct {
    FILE *f;
    uint64_t size;
    Mp4Track video, audio;
} Mp4;

int  mp4_open(Mp4 *m, const char *path, char *err, int errlen);
void mp4_close(Mp4 *m);
/* reads sample i of a track into buf (must hold s[i].size bytes) */
int  mp4_read(Mp4 *m, const Mp4Sample *s, uint8_t *buf);
/* same, through another FILE (one per thread) */
int  mp4_read_fp(FILE *f, const Mp4Sample *s, uint8_t *buf);
