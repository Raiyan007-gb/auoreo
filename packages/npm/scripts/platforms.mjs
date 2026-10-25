// Single table: npm platform package id -> release artifact name (produced by the
// PyInstaller release job; see packages/npm/README.md for the naming contract).
export const PLATFORMS = [
  { id: "linux-x64", artifact: "auoreo-linux-x64", exe: "auoreo" },
  { id: "linux-arm64", artifact: "auoreo-linux-arm64", exe: "auoreo" },
  { id: "linux-x64-musl", artifact: "auoreo-linux-x64-musl", exe: "auoreo" },
  { id: "darwin-arm64", artifact: "auoreo-macos-arm64", exe: "auoreo" },
  { id: "win32-x64", artifact: "auoreo-windows-x64.exe", exe: "auoreo.exe" },
];

export const scopedName = (id) => `@auoreo/cli-${id}`;
