export function isLocalWorkspaceOrigin(protocol: string, host: string | null, origin: string | null): boolean {
  if (!host || !origin || !["http:", "https:"].includes(protocol) || !/^(127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?$/.test(host)) return false;
  try { return origin === new URL(protocol + "//" + host).origin; } catch { return false; }
}
