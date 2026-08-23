from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


def _png(size: int) -> bytes:
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            # Paper/document mark: blue background, white page, red PDF band.
            background = (23, 50, 77, 255)
            page = x >= size * 0.18 and x <= size * 0.82 and y >= size * 0.12 and y <= size * 0.88
            fold = x >= size * 0.60 and y <= size * 0.30 and x + y >= size * 0.88
            pdf_band = page and y >= size * 0.66 and y <= size * 0.80
            if pdf_band:
                color = (220, 70, 62, 255)
            elif fold:
                color = (190, 207, 222, 255)
            elif page:
                color = (248, 250, 252, 255)
            else:
                color = background
            pixels.extend(color)

    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + chunk(b"IEND", b"")


def create_icon(path: Path) -> None:
    sizes = (16, 32, 48, 256)
    images = [_png(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    directory_size = 6 + 16 * len(images)
    entries = bytearray()
    offset = directory_size
    for size, image in zip(sizes, images):
        entries.extend(struct.pack("<BBBBHHII", 0 if size == 256 else size, 0 if size == 256 else size, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(images))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_icon(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
