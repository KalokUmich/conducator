#!/usr/bin/env python3
"""Build a Teams app package (.zip) for the Conductor bot — Phase 1.

Runs three steps:
  1. Generates two placeholder PNG icons (192x192 color, 32x32 outline) using stdlib only.
  2. Fills in the manifest template with the bot id and tunnel host from CLI args.
  3. Zips manifest + icons into `build/conductor-teams-app.zip`, ready to sideload.

Usage:
    python build.py --bot-id <App Registration Client ID> --tunnel-host <abc.ngrok-free.app>

Optional:
    --manifest-id <uuid>   Reuse a specific manifest GUID (default: read from .manifest-id or generate + persist)
    --version <semver>     App version (default: 0.1.0)
    --output <path>        Output zip path (default: build/conductor-teams-app.zip)
"""

from __future__ import annotations

import argparse
import struct
import sys
import uuid
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE / "build"
TEMPLATE = HERE / "manifest.template.json"
MANIFEST_ID_FILE = HERE / ".manifest-id"

INDIGO = (79, 70, 229, 255)  # #4F46E5


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _write_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    assert len(pixels) == width * height * 4, "pixels must be RGBA bytes"
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row_bytes = width * 4
    raw = b"".join(b"\x00" + pixels[y * row_bytes : (y + 1) * row_bytes] for y in range(height))
    idat = zlib.compress(raw, 9)
    path.write_bytes(sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))


def _solid_rgba(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    return bytes(rgba) * (width * height)


def _outline_rgba(size: int) -> bytes:
    cx = cy = size / 2 - 0.5
    r = size * 0.38
    r_sq = r * r
    white_opaque = bytes((255, 255, 255, 255))
    transparent = bytes((0, 0, 0, 0))
    out = bytearray()
    for y in range(size):
        for x in range(size):
            out += white_opaque if (x - cx) ** 2 + (y - cy) ** 2 <= r_sq else transparent
    return bytes(out)


def generate_icons() -> None:
    color_path = BUILD_DIR / "color.png"
    outline_path = BUILD_DIR / "outline.png"
    _write_png(color_path, 192, 192, _solid_rgba(192, 192, INDIGO))
    _write_png(outline_path, 32, 32, _outline_rgba(32))
    print(f"  icons: {color_path.name} (192x192), {outline_path.name} (32x32)")


def resolve_manifest_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    if MANIFEST_ID_FILE.exists():
        return MANIFEST_ID_FILE.read_text().strip()
    new_id = str(uuid.uuid4())
    MANIFEST_ID_FILE.write_text(new_id + "\n")
    print(f"  generated new manifest id: {new_id} (saved to .manifest-id for future builds)")
    return new_id


def render_manifest(bot_id: str, tunnel_host: str, manifest_id: str, version: str) -> str:
    template = TEMPLATE.read_text()
    return (
        template.replace("{{BOT_ID}}", bot_id)
        .replace("{{TUNNEL_HOST}}", tunnel_host)
        .replace("{{MANIFEST_ID}}", manifest_id)
        .replace("{{VERSION}}", version)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bot-id", required=True, help="Azure AD App Registration Client ID")
    parser.add_argument("--tunnel-host", required=True, help="Public host, e.g. abc.ngrok-free.app (no https://)")
    parser.add_argument("--manifest-id", help="Teams app manifest GUID (default: reuse or generate)")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output", default=str(BUILD_DIR / "conductor-teams-app.zip"))
    args = parser.parse_args()

    if args.tunnel_host.startswith(("http://", "https://")):
        print("ERROR: --tunnel-host must be bare host only (no scheme). E.g. abc.ngrok-free.app", file=sys.stderr)
        return 2

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1/3: generating icons")
    generate_icons()

    print("Step 2/3: rendering manifest")
    manifest_id = resolve_manifest_id(args.manifest_id)
    manifest_text = render_manifest(args.bot_id, args.tunnel_host, manifest_id, args.version)
    (BUILD_DIR / "manifest.json").write_text(manifest_text)
    print(f"  manifest.json written (botId={args.bot_id}, validDomains=[{args.tunnel_host}])")

    print("Step 3/3: packaging zip")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(BUILD_DIR / "manifest.json", "manifest.json")
        z.write(BUILD_DIR / "color.png", "color.png")
        z.write(BUILD_DIR / "outline.png", "outline.png")
    print(f"  -> {output}")
    print("\nDone. Sideload this zip in Teams: Apps -> Manage your apps -> Upload a custom app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
