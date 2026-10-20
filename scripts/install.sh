#!/usr/bin/env bash
# Download ONLY a signed-in user's release binary from the private GitHub repository.
set -euo pipefail

repo="Doradux/remoteDoradoCommander"
component="${1:-agent}"
case "$component" in agent|server) ;; *) echo 'Usage: install.sh [agent|server] [--no-setup]' >&2; exit 2 ;; esac
no_setup=false
if [[ "${2:-}" == '--no-setup' ]]; then no_setup=true; fi

if ! command -v gh >/dev/null 2>&1; then
  echo 'Install GitHub CLI (gh) and run gh auth login first: https://cli.github.com/' >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo 'Sign in with gh auth login. This repo is private.' >&2
  exit 1
fi

case "$(uname -s)" in Linux) os=linux ;; Darwin) os=macos ;; *) echo 'Use scripts/install.ps1 for Windows.' >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64|amd64) arch=x64 ;; arm64|aarch64) arch=arm64 ;; *) echo 'Unsupported CPU architecture' >&2; exit 1 ;; esac
if [[ "$os" == linux && "$arch" != x64 ]]; then
  echo 'Linux arm64 release not available yet.' >&2; exit 1
fi
if [[ "$component" == server && "$os" != linux ]]; then
  echo 'The standalone server build targets Linux x64; use Docker elsewhere.' >&2; exit 1
fi

asset="remote-dorado-${component}-${os}-${arch}.tar.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf 'Downloading %s from private GitHub Releases...\n' "$asset"
gh release download --repo "$repo" --pattern "$asset" --pattern SHA256SUMS --dir "$tmp"

expected="$(awk -v asset="$asset" '$2 == asset {print $1}' "$tmp/SHA256SUMS")"
if [[ ! "$expected" =~ ^[a-f0-9]{64}$ ]]; then echo 'Checksum missing from Release' >&2; exit 1; fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$tmp/$asset" | awk '{print $1}')"
else
  actual="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"
fi
if [[ "$actual" != "$expected" ]]; then echo 'SHA256 checksum mismatch; refusing to install' >&2; exit 1; fi

binary="remote-dorado-${component}"
# Read a single known member from the archive, never extract arbitrary paths.
tar -xOzf "$tmp/$asset" "$binary" > "$tmp/$binary"
install_dir="${DORADO_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$install_dir"
install -m 755 "$tmp/$binary" "$install_dir/$binary"
printf 'Installed: %s/%s\n' "$install_dir" "$binary"
if [[ ":$PATH:" != *":$install_dir:"* ]]; then
  printf 'Add this directory to your PATH: %s\n' "$install_dir"
fi

if [[ "$component" == agent ]] && [[ "$no_setup" == false ]] && [[ -t 0 ]]; then
  printf '\nConfigure your PC now (URL and secret token; saved only locally)? [Y/n] '
  read -r answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then "$install_dir/$binary" --setup; fi
fi
printf 'Run: %s/%s\n' "$install_dir" "$binary"
