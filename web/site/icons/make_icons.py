#!/usr/bin/env python3
"""
make_icons.py - generate the "Shelf Roulette" app icons as PNGs.

Pure standard library only (zlib + struct + math). No Pillow, no ImageMagick,
no rsvg. Geometry is analytic (radius + atan2 wedge angle) and every output
pixel is the average of a 4x4 grid of subsamples, so edges are antialiased.

Motif: a roulette wheel of book spines on deep ink navy. Ten equal wedges
alternate between brass/mustard and aged paper, split by darker navy hairlines,
with an ink-navy centre hub ringed in brass and a brass pointer at the top
aiming down into the wheel.

Rerun with:
    python3 /home/queco/bookshelf/web/site/icons/make_icons.py

Outputs into the same directory as this script:
    icon-192.png, icon-512.png, icon-maskable-512.png, favicon-32.png

The maskable variant uses a smaller wheel so all meaningful art stays inside
the centre 80 percent safe zone that Android crops to a circle.
"""

import math
import os
import struct
import zlib

# Palette
NAVY = (14, 27, 43)        # deep ink navy background
NAVY_DARK = (10, 20, 33)   # hairline between wedges / hub fill
BRASS = (201, 150, 43)     # brass / mustard spine
PAPER = (232, 219, 193)    # aged paper spine

WEDGES = 10
SUBSAMPLES = 4  # 4x4 grid per output pixel


def write_png(path, width, height, pixel_fn):
    """Write an 8 bit RGBA PNG. pixel_fn(x, y) returns an (r, g, b) tuple."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for this scanline
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            raw.append(r)
            raw.append(g)
            raw.append(b)
            raw.append(255)  # fully opaque

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def make_sampler(size, wheel_frac):
    """Return a function mapping a subsample point to an (r, g, b) colour."""
    cx = size / 2.0
    cy = size / 2.0
    wheel_r = size * wheel_frac
    hub_r = size * (0.12 if wheel_frac > 0.38 else 0.098)
    ring_w = max(size * 0.012, 1.0)
    hairline = max(size * 0.006, 0.6)
    rim_w = max(size * 0.018, 1.0)

    # Pointer: brass triangle above the wheel, tip pointing down into it.
    # It carries a dark navy outline so it stays legible where it crosses a
    # brass wedge.
    tip_y = cy - wheel_r + size * 0.085
    base_y = cy - wheel_r - size * 0.030
    half_w = size * 0.062
    outline = max(size * 0.014, 1.0)

    def in_pointer(px, py, pad):
        top = base_y - pad
        bottom = tip_y + pad
        if py < top or py > bottom:
            return False
        # Linear taper from full width at the base down to a point at the tip.
        t = (py - top) / (bottom - top)
        w = (half_w + pad) * (1.0 - t)
        return abs(px - cx) <= w

    def sample(px, py):
        if in_pointer(px, py, outline):
            return BRASS if in_pointer(px, py, 0.0) else NAVY_DARK

        dx = px - cx
        dy = py - cy
        dist = math.hypot(dx, dy)

        if dist > wheel_r:
            return NAVY

        # Outer rim of the wheel in brass, for definition against the navy,
        # then a dark hairline so brass wedges do not merge into the rim.
        if dist > wheel_r - rim_w:
            return BRASS
        if dist > wheel_r - rim_w - hairline:
            return NAVY_DARK

        if dist <= hub_r:
            if dist > hub_r - ring_w:
                return BRASS  # thin brass ring around the hub
            return NAVY_DARK  # hub interior

        # Wedge index from the angle, measured clockwise from straight up.
        ang = math.atan2(dx, -dy) % (2.0 * math.pi)
        step = 2.0 * math.pi / WEDGES
        pos = ang / step
        idx = int(pos)
        frac = pos - idx

        # Hairline separators: constant arc width in pixels near this radius.
        if dist > 1e-6:
            hair_frac = (hairline / dist) / step
        else:
            hair_frac = 1.0
        if frac < hair_frac or frac > 1.0 - hair_frac:
            return NAVY_DARK

        return BRASS if idx % 2 == 0 else PAPER

    return sample


def render(path, size, wheel_frac):
    sample = make_sampler(size, wheel_frac)
    n = SUBSAMPLES
    offs = [(i + 0.5) / n for i in range(n)]
    total = n * n

    def pixel(x, y):
        r = g = b = 0
        for oy in offs:
            py = y + oy
            for ox in offs:
                sr, sg, sb = sample(x + ox, py)
                r += sr
                g += sg
                b += sb
        return (r // total, g // total, b // total)

    write_png(path, size, size, pixel)
    print("wrote %s (%dx%d)" % (path, size, size))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    render(os.path.join(here, "icon-192.png"), 192, 0.42)
    render(os.path.join(here, "icon-512.png"), 512, 0.42)
    render(os.path.join(here, "icon-maskable-512.png"), 512, 0.34)
    render(os.path.join(here, "favicon-32.png"), 32, 0.42)


if __name__ == "__main__":
    main()
