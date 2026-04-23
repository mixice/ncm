"""
NCM 文件解密核心模块
NCM 格式说明:
  - Magic: 8 bytes (43 54 45 4E 46 44 41 4D / CTENFDAM)
  - 2 bytes gap
  - Key data length (4 bytes LE) + encrypted key data
  - Meta data length (4 bytes LE) + encrypted meta data
  - CRC32 (4 bytes) + 5 bytes gap
  - Album image size (4 bytes) + image data
  - Audio data (RC4 encrypted)
"""

import struct
import base64
import json
import os
from Crypto.Cipher import AES


CORE_KEY = bytes([
    0x68, 0x7A, 0x48, 0x52, 0x41, 0x6D, 0x73, 0x6F,
    0x35, 0x6B, 0x49, 0x6E, 0x62, 0x61, 0x78, 0x57
])

META_KEY = bytes([
    0x23, 0x31, 0x34, 0x6C, 0x6A, 0x6B, 0x5F, 0x21,
    0x5C, 0x5D, 0x26, 0x30, 0x55, 0x3C, 0x27, 0x28
])

MAGIC = b'CTENFDAM'


def _aes_decrypt(data: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(data)


def _unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    return data[:-pad_len]


def _build_key_box(key: bytes) -> bytes:
    """Build NCM's non-standard RC4 key box (256 bytes, used as repeating XOR pad).

    Steps:
      1. Standard RC4 KSA to produce S-box
      2. Non-standard keystream: key_box[i] = s_box[(s_box[j] + s_box[(s_box[j] + j) & 0xFF]) & 0xFF]
         where j = (i + 1) & 0xFF
    """
    key_len = len(key)
    s_box = bytearray(range(256))

    # KSA
    j = 0
    for i in range(256):
        j = (j + s_box[i] + key[i % key_len]) & 0xFF
        s_box[i], s_box[j] = s_box[j], s_box[i]

    # NCM keystream generation (NOT standard PRGA — no swaps)
    key_box = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        s_j = s_box[j]
        s_jj = s_box[(s_j + j) & 0xFF]
        key_box[i] = s_box[(s_jj + s_j) & 0xFF]

    return bytes(key_box)


class NcmFile:
    def __init__(self):
        self.format = None       # 'mp3' or 'flac'
        self.meta = {}           # parsed JSON metadata
        self.cover_data = None   # album cover bytes
        self._key_box = None     # RC4 keystream

    def load(self, path: str):
        with open(path, 'rb') as f:
            magic = f.read(8)
            if magic != MAGIC:
                raise ValueError(f'不是有效的 NCM 文件 (magic 不匹配): {path}')
            f.read(2)  # gap

            # --- Key ---
            key_len = struct.unpack('<I', f.read(4))[0]
            key_data = bytearray(f.read(key_len))
            for i in range(key_len):
                key_data[i] ^= 0x64
            dec_key = _unpad(_aes_decrypt(bytes(key_data), CORE_KEY))
            # dec_key starts with 'neteasecloudmusic'
            rc4_key = dec_key[17:]
            self._key_box = _build_key_box(rc4_key)

            # --- Meta ---
            meta_len = struct.unpack('<I', f.read(4))[0]
            if meta_len > 0:
                meta_data = bytearray(f.read(meta_len))
                for i in range(meta_len):
                    meta_data[i] ^= 0x63
                # meta_data starts with '163 key(Don't modify):'
                meta_b64 = bytes(meta_data[22:])
                meta_dec = _unpad(_aes_decrypt(base64.b64decode(meta_b64), META_KEY))
                # starts with 'music:'
                self.meta = json.loads(meta_dec[6:])
                self.format = self.meta.get('format', 'mp3').lower()
            else:
                f.read(meta_len)
                self.format = 'mp3'

            # --- CRC + gap ---
            f.read(9)

            # --- Cover image ---
            img_size = struct.unpack('<I', f.read(4))[0]
            if img_size > 0:
                self.cover_data = f.read(img_size)
            else:
                self.cover_data = None

            # --- Audio data ---
            self._audio_offset = f.tell()
            self._audio_len = os.path.getsize(path) - self._audio_offset
            self._path = path

    def dump_audio(self, out_path: str, progress_cb=None):
        """Decrypt and write audio to out_path."""
        key_box = self._key_box
        key_len = len(key_box)
        chunk = 0x8000  # 32KB

        with open(self._path, 'rb') as fin:
            fin.seek(self._audio_offset)
            with open(out_path, 'wb') as fout:
                processed = 0
                while True:
                    data = bytearray(fin.read(chunk))
                    if not data:
                        break
                    for i in range(len(data)):
                        data[i] ^= key_box[(processed + i) % key_len]
                    fout.write(data)
                    processed += len(data)
                    if progress_cb:
                        progress_cb(processed, self._audio_len)

    def write_tags(self, out_path: str):
        """Write ID3/FLAC tags using mutagen."""
        try:
            if self.format == 'mp3':
                self._write_mp3_tags(out_path)
            elif self.format == 'flac':
                self._write_flac_tags(out_path)
        except Exception as e:
            pass  # Tags are optional; don't fail on tag errors

    def _write_mp3_tags(self, path: str):
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, APIC, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()

        m = self.meta
        if m.get('musicName'):
            tags['TIT2'] = TIT2(encoding=3, text=m['musicName'])
        if m.get('artist'):
            artists = '/'.join(a[0] for a in m['artist'] if a)
            tags['TPE1'] = TPE1(encoding=3, text=artists)
        if m.get('album'):
            tags['TALB'] = TALB(encoding=3, text=m['album'])
        if m.get('trackNumber'):
            tags['TRCK'] = TRCK(encoding=3, text=str(m['trackNumber']))
        if self.cover_data:
            mime = 'image/jpeg'
            if self.cover_data[:4] == b'\x89PNG':
                mime = 'image/png'
            tags['APIC'] = APIC(
                encoding=3, mime=mime, type=3,
                desc='Cover', data=self.cover_data
            )
        tags.save(path, v2_version=3)

    def _write_flac_tags(self, path: str):
        from mutagen.flac import FLAC, Picture
        audio = FLAC(path)
        m = self.meta
        if m.get('musicName'):
            audio['title'] = m['musicName']
        if m.get('artist'):
            audio['artist'] = '/'.join(a[0] for a in m['artist'] if a)
        if m.get('album'):
            audio['album'] = m['album']
        if self.cover_data:
            pic = Picture()
            pic.type = 3
            pic.mime = 'image/jpeg'
            if self.cover_data[:4] == b'\x89PNG':
                pic.mime = 'image/png'
            pic.data = self.cover_data
            audio.add_picture(pic)
        audio.save()


def convert(ncm_path: str, output_dir: str = None, progress_cb=None) -> str:
    """
    Convert a single NCM file.
    Returns the output file path on success.
    Raises exceptions on failure.
    """
    ncm = NcmFile()
    ncm.load(ncm_path)

    base = os.path.splitext(os.path.basename(ncm_path))[0]
    ext = ncm.format if ncm.format in ('mp3', 'flac') else 'mp3'
    if output_dir is None:
        output_dir = os.path.dirname(ncm_path)
    out_path = os.path.join(output_dir, base + '.' + ext)

    ncm.dump_audio(out_path, progress_cb=progress_cb)
    ncm.write_tags(out_path)
    return out_path
