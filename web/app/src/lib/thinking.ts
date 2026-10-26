import { useState } from "react";

const KEY = "auoreo.showThinking";

function read(): boolean {
  try {
    return localStorage.getItem(KEY) !== "0";
  } catch {
    return true;
  }
}

/** Per-user (per-browser) preference: show the model's thinking blocks. Default on. */
export function useShowThinking(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(read);
  const set = (v: boolean) => {
    setOn(v);
    try {
      localStorage.setItem(KEY, v ? "1" : "0");
    } catch {
      /* storage blocked: preference lasts for this page only */
    }
  };
  return [on, set];
}
