const key = "hotradar:platform-session-updated";

export function notifyPlatformSessionUpdated() {
  try { localStorage.setItem(key, String(Date.now())); } catch { /* Storage can be disabled. */ }
}

export function watchPlatformSessionUpdates(refresh: () => void) {
  const onStorage = (event: StorageEvent) => { if (event.key === key) refresh(); };
  const onVisible = () => { if (document.visibilityState === "visible") refresh(); };
  window.addEventListener("storage", onStorage);
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.removeEventListener("storage", onStorage);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
