"""Generate an auditable x64 Windows executable: ExitProcess(42). No cross compiler needed."""
from pathlib import Path
import struct
import sys


def windows_probe(path):
    data=bytearray(1024)
    data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80)
    data[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<HHIIIHH',data,0x84,0x8664,1,0,0,0,240,0x22)
    optional=0x98
    struct.pack_into('<HBBIIIIIQ',data,optional,0x20b,14,0,512,0,0,0x1000,0x1000,0x140000000)
    struct.pack_into('<IIHHHHHHIIIIHHQQQQII',data,optional+32,
                     0x1000,0x200,6,0,0,0,6,0,0,0x2000,0x200,0,3,0x100,0x100000,0x1000,0x100000,0x1000,0,16)
    struct.pack_into('<II',data,optional+112+8,0x1080,40)
    struct.pack_into('<II',data,optional+112+12*8,0x10d0,16)
    section=optional+240
    struct.pack_into('<8sIIIIIIHHI',data,section,b'.text\0\0\0',0x200,0x1000,0x200,0x200,0,0,0,0,0xe0000020)
    data[0x200:0x20f]=bytes.fromhex('48 83 ec 28 b9 2a 00 00 00 ff 15 c1 00 00 00')
    struct.pack_into('<IIIII',data,0x280,0x10c0,0,0,0x10a8,0x10d0)
    data[0x2a8:0x2b5]=b'kernel32.dll\0'
    struct.pack_into('<QQ',data,0x2c0,0x10e0,0);struct.pack_into('<QQ',data,0x2d0,0x10e0,0)
    data[0x2e0:0x2ee]=b'\0\0ExitProcess\0'
    Path(path).write_bytes(data)


if __name__=='__main__':
    windows_probe(sys.argv[1])
