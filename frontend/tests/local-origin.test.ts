import { test } from "node:test";
import assert from "node:assert/strict";
import { isLocalWorkspaceOrigin } from "../src/lib/local-origin.ts";

test("loopback origin uses actual request host instead of Next internal localhost URL", () => {
  assert.equal(isLocalWorkspaceOrigin("http:", "127.0.0.1:18811", "http://127.0.0.1:18811"), true);
  assert.equal(isLocalWorkspaceOrigin("http:", "localhost:18811", "http://localhost:18811"), true);
});
test("foreign, missing, spoofed host and other-port origins cannot change sources", () => {
  assert.equal(isLocalWorkspaceOrigin("http:", "127.0.0.1:18811", "https://external.invalid"), false);
  assert.equal(isLocalWorkspaceOrigin("http:", "127.0.0.1:18811", null), false);
  assert.equal(isLocalWorkspaceOrigin("http:", "external.invalid", "http://external.invalid"), false);
  assert.equal(isLocalWorkspaceOrigin("http:", "127.0.0.1:18811", "http://127.0.0.1:18812"), false);
});
