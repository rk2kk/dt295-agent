/*
 * dt295-recover: PIX-DT295 の ASSP を「起動できる状態」に戻す小さな道具。
 *
 * 参照実装 (pix-ts-legacy) は状態 0 (コールド) か 0x10 (未認証 ready) からしか
 * ファームを起動しない。正常終了でも 0x11 (認証済み idle) に残ることがあり、
 * そのままでは次の選局が "firmware boot requires cold state" で失敗する。
 * 公式ドライバの復旧コマンド 3 を 1 回だけ送って 0x10 に戻す。
 *
 *   dt295-recover [serial]      # 省略時は先頭の 06b8:103b
 *   dt295-recover --state       # 状態を表示するだけ
 *
 * 終了コード: 0 = 起動可能 (0x00/0x10)、1 = 復旧した、2 = 復旧できず、3 = デバイス無し
 */
#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VID 0x06b8
#define PID 0x103b
#define REG_STATE 0x82008

static int read_state(libusb_device_handle *h, unsigned *state) {
    unsigned char b[2] = {0, 0};
    int r = libusb_control_transfer(h, 0xc0, 0xbc, (uint16_t)((REG_STATE >> 16) << 8),
                                    (uint16_t)REG_STATE, b, sizeof(b), 1000);
    if (r != (int)sizeof(b))
        return -1;
    *state = ((unsigned)b[0] << 8) | b[1];
    return 0;
}

static int command(libusb_device_handle *h, unsigned cmd) {
    unsigned char data[4] = {(unsigned char)cmd, 0, 0, 0};
    return libusb_control_transfer(h, 0x40, 0xb8, 0, 0, data, sizeof(data), 1000);
}

int main(int argc, char **argv) {
    int state_only = 0;
    const char *want = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--state"))
            state_only = 1;
        else
            want = argv[i];
    }

    libusb_context *ctx = NULL;
    if (libusb_init(&ctx) != 0) {
        fprintf(stderr, "libusb init failed\n");
        return 3;
    }

    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx, &list);
    libusb_device_handle *handle = NULL;
    for (ssize_t i = 0; i < n && !handle; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(list[i], &desc))
            continue;
        if (desc.idVendor != VID || desc.idProduct != PID)
            continue;
        libusb_device_handle *h = NULL;
        if (libusb_open(list[i], &h))
            continue;
        if (want) {
            char serial[64] = {0};
            if (desc.iSerialNumber &&
                libusb_get_string_descriptor_ascii(h, desc.iSerialNumber, (unsigned char *)serial,
                                                   sizeof(serial) - 1) > 0 &&
                strcmp(serial, want)) {
                libusb_close(h);
                continue;
            }
        }
        handle = h;
    }
    libusb_free_device_list(list, 1);
    if (!handle) {
        fprintf(stderr, "no DT295 found\n");
        libusb_exit(ctx);
        return 3;
    }

    if (libusb_kernel_driver_active(handle, 0) == 1)
        (void)libusb_detach_kernel_driver(handle, 0);
    if (libusb_claim_interface(handle, 0)) {
        fprintf(stderr, "claim interface failed\n");
        libusb_close(handle);
        libusb_exit(ctx);
        return 3;
    }

    unsigned state = 0;
    if (read_state(handle, &state)) {
        fprintf(stderr, "state read failed\n");
        libusb_release_interface(handle, 0);
        libusb_close(handle);
        libusb_exit(ctx);
        return 2;
    }

    if (state == 0x0000 || state == 0x0010) {
        printf("state=0x%04x bootable\n", state);
        libusb_release_interface(handle, 0);
        libusb_close(handle);
        libusb_exit(ctx);
        return 0;
    }

    printf("state=0x%04x not bootable\n", state);
    if (state_only) {
        libusb_release_interface(handle, 0);
        libusb_close(handle);
        libusb_exit(ctx);
        return 2;
    }

    if (command(handle, 3) < 0) {
        fprintf(stderr, "command 3 failed\n");
        libusb_release_interface(handle, 0);
        libusb_close(handle);
        libusb_exit(ctx);
        return 2;
    }

    /* 復旧は非同期。状態が変わるまで少し待つ。 */
    int rc = 2;
    for (int i = 0; i < 40; i++) {
        struct timespec pause = {0, 100 * 1000 * 1000};
        nanosleep(&pause, NULL);
        if (read_state(handle, &state))
            break;
        if (state == 0x0000 || state == 0x0010) {
            printf("recovered to state=0x%04x\n", state);
            rc = 1;
            break;
        }
    }
    if (rc != 1)
        printf("recovery did not converge (state=0x%04x)\n", state);

    libusb_release_interface(handle, 0);
    libusb_close(handle);
    libusb_exit(ctx);
    return rc;
}
