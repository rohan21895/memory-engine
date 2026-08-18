import { describe, expect, it, vi } from "vitest";

import type { ConsentGrant, ConsentLedger, EgressTransport } from "../src/egress.js";
import {
  assertTelemetryPayloadSafe,
  ConsentBoundEgress,
} from "../src/egress.js";

const consent = {
  ledger_entry_id: "c4c16e66-3b0d-4a9a-9d2d-53d956d8701d",
  scope: "tier3_contact_sheet" as const,
  granted_at: "2026-08-18T00:00:00.000Z",
};

function grant(overrides: Partial<ConsentGrant> = {}): ConsentGrant {
  return {
    consent,
    destination: "tier3_inference",
    payload_kind: "contact_sheet",
    allowed_origin: "https://inference.example",
    ...overrides,
  };
}

function ledger(value: ConsentGrant | null): ConsentLedger {
  return { find: async () => value };
}

function request(consentOverride = consent) {
  return {
    declaration: {
      requires_egress: true,
      consent: consentOverride,
      destination: "tier3_inference" as const,
      payload_kind: "contact_sheet" as const,
    },
    url: "https://inference.example/v1/decide",
  };
}

describe("ConsentBoundEgress", () => {
  it("does not invoke its transport for an absent ledger entry", async () => {
    const transport = vi.fn<EgressTransport>();
    const egress = new ConsentBoundEgress(ledger(null), { transport });

    await expect(egress.request(request())).rejects.toThrow(/No consent-ledger entry/);
    expect(transport).not.toHaveBeenCalled();
  });

  it("rejects a forged consent ref even when its ledger id exists", async () => {
    const transport = vi.fn<EgressTransport>();
    const egress = new ConsentBoundEgress(ledger(grant()), { transport });

    await expect(
      egress.request(request({ ...consent, granted_at: "2026-08-17T00:00:00.000Z" })),
    ).rejects.toThrow(/does not match/);
    expect(transport).not.toHaveBeenCalled();
  });

  it("rejects revoked and expired grants before opening a connection", async () => {
    const transport = vi.fn<EgressTransport>();
    const now = () => new Date("2026-08-18T12:00:00.000Z");
    const revokedConsent = { ...consent, revoked_at: "2026-08-18T10:00:00.000Z" };
    const revoked = new ConsentBoundEgress(ledger(grant({ consent: revokedConsent })), { transport, now });
    await expect(revoked.request(request(revokedConsent))).rejects.toThrow(/revoked/);

    const expiredConsent = { ...consent, expires_at: "2026-08-18T10:00:00.000Z" };
    const expired = new ConsentBoundEgress(ledger(grant({ consent: expiredConsent })), { transport, now });
    await expect(expired.request(request(expiredConsent))).rejects.toThrow(/expired/);
    expect(transport).not.toHaveBeenCalled();
  });

  it("runs telemetry privacy filtering before invoking its transport", async () => {
    const transport = vi.fn<EgressTransport>();
    const telemetryGrant = grant({
      destination: "telemetry",
      payload_kind: "metadata_only",
      allowed_origin: "https://telemetry.example",
    });
    const egress = new ConsentBoundEgress(ledger(telemetryGrant), { transport });

    await expect(
      egress.request({
        declaration: {
          requires_egress: true,
          consent,
          destination: "telemetry",
          payload_kind: "metadata_only",
        },
        url: "https://telemetry.example/events",
        init: { body: JSON.stringify({ exif: { make: "CameraCo" } }) },
      }),
    ).rejects.toThrow(/private metadata/);
    expect(transport).not.toHaveBeenCalled();
  });
});

describe("telemetry privacy filter", () => {
  it("rejects opaque containers, bytes, and Windows paths", () => {
    expect(() => assertTelemetryPayloadSafe({ attachment: new Uint8Array([1, 2, 3]) })).toThrow(/opaque bytes/);
    expect(() => assertTelemetryPayloadSafe(new URLSearchParams({ path: "/private/photo.jpg" }))).toThrow(
      /unsupported payload container/,
    );
    expect(() => assertTelemetryPayloadSafe({ message: "failed at C:\\Photos\\IMG_0042.jpg" })).toThrow(
      /local path or media filename/,
    );
  });
});
