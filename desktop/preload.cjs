// Sandboxed preload: expose only static app metadata, never Node or IPC handles.
// Values arrive through webPreferences.additionalArguments (see main.cjs).
"use strict";

const { contextBridge } = require("electron");

function arg(name) {
  const prefix = `--auoreo-${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld("auoreoDesktop", Object.freeze({ version: arg("version"), channel: arg("channel") }));
