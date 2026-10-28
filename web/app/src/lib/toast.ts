/** A tiny toast store: `toast.success("Saved")` from anywhere; `<Toaster />` (components/ui/toast) renders it. */
export type ToastLevel = "success" | "info" | "error";
export interface ToastItem {
  id: number;
  level: ToastLevel;
  text: string;
}

const listeners = new Set<(t: ToastItem[]) => void>();
let items: ToastItem[] = [];
let next = 1;
const DURATION_MS = 3500;

function emit() {
  for (const l of listeners) l(items);
}

function push(level: ToastLevel, text: string) {
  const id = next++;
  items = [...items.slice(-3), { id, level, text }];
  emit();
  setTimeout(() => dismissToast(id), level === "error" ? DURATION_MS * 2 : DURATION_MS);
}

export function dismissToast(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(fn: (t: ToastItem[]) => void): () => void {
  listeners.add(fn);
  fn(items);
  return () => {
    listeners.delete(fn);
  };
}

export const toast = {
  success: (text: string) => push("success", text),
  info: (text: string) => push("info", text),
  error: (text: string) => push("error", text),
};
