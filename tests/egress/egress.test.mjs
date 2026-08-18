import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";

import {
  assertTelemetryPayloadSafe,
  ConsentBoundEgress,
} from "../../services/api/dist/services/api/src/egress.js";

const CONSENT = {
  ledger_entry_id: "c4c16e66-3b0d-4a9a-9d2d-53d956d8701d",
  scope: "tier3_contact_sheet",
  granted_at: "2026-08-18T00:00:00.000Z",
};

function declaration(overrides = {}) {
  return {
    requires_egress: true,
    consent: CONSENT,
    destination: "tier3_inference",
    payload_kind: "contact_sheet",
    ...overrides,
  };
}

function ledger(grants) {
  return {
    async find(id) {
      return grants.get(id) ?? null;
    },
  };
}

async function loopback(t) {
  let connections = 0;
  const server = createServer((_request, response) => {
    connections += 1;
    response.writeHead(204).end();
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return {
    origin: `http://127.0.0.1:${address.port}`,
    connections: () => connections,
  };
}

test("blocks an outbound connection without a consent-ledger entry", async (t) => {
  const target = await loopback(t);
  const egress = new ConsentBoundEgress(ledger(new Map()), {
    now: () => new Date("2026-08-18T12:00:00.000Z"),
  });

  await assert.rejects(
    egress.request({ declaration: declaration(), url: `${target.origin}/infer` }),
    /No consent-ledger entry/,
  );
  assert.equal(target.connections(), 0, "the denied request reached the network");
});

test("allows only the destination and payload class recorded by consent", async (t) => {
  const target = await loopback(t);
  const grants = new Map([
    [
      CONSENT.ledger_entry_id,
      {
        consent: CONSENT,
        destination: "tier3_inference",
        payload_kind: "contact_sheet",
        allowed_origin: target.origin,
      },
    ],
  ]);
  const egress = new ConsentBoundEgress(ledger(grants), {
    now: () => new Date("2026-08-18T12:00:00.000Z"),
  });

  const response = await egress.request({ declaration: declaration(), url: `${target.origin}/infer` });
  assert.equal(response.status, 204);
  assert.equal(target.connections(), 1);

  await assert.rejects(
    egress.request({
      declaration: declaration({ destination: "cloud_render" }),
      url: `${target.origin}/infer`,
    }),
    /destination is outside/,
  );
  await assert.rejects(
    egress.request({
      declaration: declaration({ payload_kind: "original_media" }),
      url: `${target.origin}/infer`,
    }),
    /payload class is outside/,
  );
  await assert.rejects(
    egress.request({ declaration: declaration(), url: "http://127.0.0.1:9/infer" }),
    /origin is outside/,
  );
  assert.equal(target.connections(), 1, "a mismatched grant reached the network");
});

test("rejects paths, filenames, and EXIF in crash or analytics payloads", () => {
  assert.throws(
    () => assertTelemetryPayloadSafe({ error: { absolute_path: "/private/media/IMG_0042.jpg" } }),
    /private metadata/,
  );
  assert.throws(
    () => assertTelemetryPayloadSafe({ breadcrumbs: ["decoder rejected IMG_0042.HEIC"] }),
    /local path or media filename/,
  );
  assert.throws(
    () => assertTelemetryPayloadSafe({ context: { exif: { make: "CameraCo" } } }),
    /private metadata/,
  );
  assert.throws(
    () => assertTelemetryPayloadSafe({ context: { gps_latitude: 15.498 } }),
    /private metadata/,
  );
  assert.doesNotThrow(() =>
    assertTelemetryPayloadSafe({ error_code: "unsupported_codec", worker: "video_proxy", retryable: false }),
  );
});
