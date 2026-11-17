import { test } from "node:test";
import assert from "node:assert/strict";
import { publicOriginAllowed } from "../src/lib/public-origin.ts";

const production = "https://hotradar.tech-atm.com";

test("the public workbench accepts only the configured HTTPS origin", () => {
  assert.equal(publicOriginAllowed(production, production), true);
  assert.equal(publicOriginAllowed(production, "https://www.tech-atm.com"), false);
  assert.equal(publicOriginAllowed(production, "https://hotradar.gywhmcn.cn"), false);
  assert.equal(publicOriginAllowed(production, "https://hotradar.gwyhmcn.cn"), false);
  assert.equal(publicOriginAllowed(production, "http://hotradar.tech-atm.com"), false);
  assert.equal(publicOriginAllowed(production + "/", production), false);
  assert.equal(publicOriginAllowed(undefined, production), false);
  assert.equal(publicOriginAllowed(production, null), false);
});
