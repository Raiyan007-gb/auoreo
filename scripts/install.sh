#!/bin/sh
# AUOREO installer (spec §24.10 L893): download the native binary for this OS/arch from the
# signed update manifest, verify the ed25519 signature and the sha256, install to ~/.local/bin.
#
#   curl -fsSL https://github.com/Raiyan007-gb/auoreo/releases/latest/download/install.sh | sh
#   sh install.sh [--channel stable|beta|dev] [--dir DIR]
#
# Environment (flags win):
#   AUOREO_CHANNEL        stable (default) | beta | dev
#   AUOREO_INSTALL_DIR    target directory (default: $HOME/.local/bin)
#   AUOREO_MANIFEST_URL   manifest URL; `{channel}` is replaced (default: the rolling `updates`
#                         release that `auoreo upgrade` reads). file:// works with curl.
#   AUOREO_PUBKEYS        space-separated base64 ed25519 keys replacing the pinned release key
#                         (self-hosted mirrors only).
#   AUOREO_PLATFORM       override detection: linux-x64 | linux-arm64 | linux-x64-musl | macos-arm64
# Needs: curl or wget, python3 (canonical JSON), openssl >= 1.1.1 (ed25519). Nothing is
# installed unless the manifest signature AND the binary's sha256 both verify.
set -eu

PINNED_PUBKEYS="dKWsJip0NPV09nDE/9z7qM7MCR/oY26gdzaKVtvzYgM="
DEFAULT_MANIFEST="https://github.com/Raiyan007-gb/auoreo/releases/download/updates/manifest-{channel}.json"

channel="${AUOREO_CHANNEL:-stable}"
dir="${AUOREO_INSTALL_DIR:-${HOME}/.local/bin}"
manifest_url="${AUOREO_MANIFEST_URL:-$DEFAULT_MANIFEST}"
pubkeys="${AUOREO_PUBKEYS:-$PINNED_PUBKEYS}"

say() { printf 'auoreo-install: %s\n' "$*" >&2; }
die() { say "error: $*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --channel) [ $# -ge 2 ] || die "--channel needs a value"; channel="$2"; shift 2 ;;
    --dir) [ $# -ge 2 ] || die "--dir needs a value"; dir="$2"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$channel" in
  stable|beta|dev) ;;
  *) die "channel must be stable, beta or dev (got '$channel')" ;;
esac

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }
need python3
need openssl
need uname

detect_platform() {
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) die "unsupported CPU architecture: $arch" ;;
  esac
  case "$os" in
    Linux)
      libc=""
      if ls /lib/ld-musl-* >/dev/null 2>&1; then libc="-musl"; fi
      if [ -n "$libc" ] && [ "$arch" != x64 ]; then die "no musl build for $arch"; fi
      printf 'linux-%s%s\n' "$arch" "$libc" ;;
    Darwin)
      [ "$arch" = arm64 ] || die "only Apple Silicon (arm64) macOS builds are published"
      printf 'macos-arm64\n' ;;
    *) die "unsupported OS: $os (Windows: download auoreo-windows-x64.exe from the release page)" ;;
  esac
}

fetch() {  # fetch URL OUT
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --proto '=https,file' --retry 2 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    case "$1" in https://*) ;; *) die "wget can only fetch https:// URLs (got $1)" ;; esac
    wget -q -O "$2" "$1"
  else
    die "curl or wget is required"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d ' ' -f 1
  else
    openssl dgst -sha256 -r "$1" | cut -d ' ' -f 1
  fi
}

platform="${AUOREO_PLATFORM:-$(detect_platform)}"
tmp="$(mktemp -d 2>/dev/null || mktemp -d -t auoreo-install)"
trap 'rm -rf "$tmp"' EXIT INT TERM

url="$(printf '%s' "$manifest_url" | sed "s/{channel}/$channel/g")"
say "manifest $url"
fetch "$url" "$tmp/manifest.json" || die "cannot download the manifest"

# canonical JSON (sorted keys, compact, UTF-8, without "sig") + the signature + this asset
python3 - "$tmp" "$channel" "$platform" <<'PY' || die "manifest is malformed or not for this channel/platform"
import base64, json, sys
tmp, channel, platform = sys.argv[1:4]
doc = json.load(open(f"{tmp}/manifest.json", encoding="utf-8"))
sig = doc.pop("sig", None)
if not isinstance(sig, str):
    sys.exit("manifest has no signature")
canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
open(f"{tmp}/canon.json", "wb").write(canon)
open(f"{tmp}/sig.bin", "wb").write(base64.b64decode(sig))
if doc.get("channel") != channel:
    sys.exit(f"manifest channel {doc.get('channel')!r} != {channel!r}")
asset = (doc.get("assets") or {}).get(platform)
if not asset or "url" not in asset or "sha256" not in asset:
    sys.exit(f"release {doc.get('version')} has no asset for {platform}")
with open(f"{tmp}/asset.env", "w") as fh:
    fh.write(f"{doc['version']}\n{asset['url']}\n{asset['sha256'].lower()}\n")
PY

verified=""
for key in $pubkeys; do
  python3 - "$tmp/pub.pem" "$key" <<'PY' || continue
import base64, sys
raw = base64.b64decode(sys.argv[2])
if len(raw) != 32:
    sys.exit(1)
der = bytes.fromhex("302a300506032b6570032100") + raw
pem = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n"
open(sys.argv[1], "w").write(pem)
PY
  if openssl pkeyutl -verify -pubin -inkey "$tmp/pub.pem" -rawin -in "$tmp/canon.json" \
      -sigfile "$tmp/sig.bin" >/dev/null 2>&1; then
    verified=1
    break
  fi
done
[ -n "$verified" ] || die "manifest signature does not verify against the pinned release key(s)"

version="$(sed -n 1p "$tmp/asset.env")"
asset_url="$(sed -n 2p "$tmp/asset.env")"
want="$(sed -n 3p "$tmp/asset.env")"
say "auoreo $version ($channel) for $platform"
fetch "$asset_url" "$tmp/auoreo" || die "cannot download $asset_url"
have="$(sha256_of "$tmp/auoreo")"
[ "$have" = "$want" ] || die "sha256 mismatch for $asset_url (got $have, manifest says $want)"

mkdir -p "$dir"
chmod 0755 "$tmp/auoreo"
mv -f "$tmp/auoreo" "$dir/auoreo.tmp.$$"
mv -f "$dir/auoreo.tmp.$$" "$dir/auoreo"
say "installed $dir/auoreo ($version)"
case ":${PATH}:" in
  *":$dir:"*) ;;
  *) say "add $dir to PATH, e.g. export PATH=\"$dir:\$PATH\"" ;;
esac
