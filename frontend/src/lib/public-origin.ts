// The workbench allowlist is the deployment value of HOTRADAR_PUBLIC_ORIGIN.
// Production uses https://hotradar.tech-atm.com. The old hotradar.gywhmcn.cn name
// is not a fallback and is not accepted unless that exact string is configured.
export function publicOriginAllowed(configured: string | undefined, origin: string | null): boolean {
  return Boolean(configured && configured.startsWith("https://") && origin === configured);
}
