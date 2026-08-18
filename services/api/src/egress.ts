import type {
  ConsentRef,
  EgressDeclaration,
  EgressDeclarationDestination,
  EgressDeclarationPayloadKind,
} from "../../../contracts/codegen/generated/typescript/index.js";

export interface ConsentGrant {
  consent: ConsentRef;
  destination: EgressDeclarationDestination;
  payload_kind: EgressDeclarationPayloadKind;
  /** Normalized URL origin, including scheme and port, that may receive the payload. */
  allowed_origin: string;
}

export interface ConsentLedger {
  find(ledgerEntryId: string): Promise<ConsentGrant | null>;
}

export interface EgressRequest {
  declaration: EgressDeclaration;
  url: string | URL;
  init?: RequestInit;
}

export type EgressTransport = (input: string | URL, init?: RequestInit) => Promise<Response>;

export class EgressDenied extends Error {
  readonly code = "egress_denied";

  constructor(message: string) {
    super(message);
    this.name = "EgressDenied";
  }
}

function sameConsent(declared: ConsentRef, recorded: ConsentRef): boolean {
  return (
    declared.ledger_entry_id === recorded.ledger_entry_id &&
    declared.scope === recorded.scope &&
    declared.granted_at === recorded.granted_at &&
    (declared.expires_at ?? null) === (recorded.expires_at ?? null) &&
    (declared.revoked_at ?? null) === (recorded.revoked_at ?? null)
  );
}

function assertLive(consent: ConsentRef, now: Date): void {
  if (consent.revoked_at != null) {
    throw new EgressDenied("The consent-ledger entry has been revoked.");
  }
  if (consent.expires_at != null && Date.parse(consent.expires_at) <= now.getTime()) {
    throw new EgressDenied("The consent-ledger entry has expired.");
  }
  if (Date.parse(consent.granted_at) > now.getTime()) {
    throw new EgressDenied("The consent-ledger entry is not active yet.");
  }
}

function normalizedOrigin(value: string | URL): string {
  let url: URL;
  try {
    url = value instanceof URL ? value : new URL(value);
  } catch {
    throw new EgressDenied("The egress destination is not a valid absolute URL.");
  }
  if (url.username !== "" || url.password !== "") {
    throw new EgressDenied("Credentials are not permitted in an egress destination URL.");
  }
  return url.origin;
}

export class ConsentBoundEgress {
  readonly #ledger: ConsentLedger;
  readonly #transport: EgressTransport;
  readonly #now: () => Date;

  constructor(
    ledger: ConsentLedger,
    options: { transport?: EgressTransport; now?: () => Date } = {},
  ) {
    this.#ledger = ledger;
    this.#transport = options.transport ?? fetch;
    this.#now = options.now ?? (() => new Date());
  }

  async request(request: EgressRequest): Promise<Response> {
    const declaration = request.declaration;
    if (declaration.requires_egress !== true) {
      throw new EgressDenied("A network request must explicitly declare that it requires egress.");
    }
    if (declaration.consent == null || declaration.destination == null || declaration.payload_kind == null) {
      throw new EgressDenied("A network request requires consent, destination, and payload class.");
    }

    const grant = await this.#ledger.find(declaration.consent.ledger_entry_id);
    if (grant == null) {
      throw new EgressDenied("No consent-ledger entry authorizes this network request.");
    }
    if (!sameConsent(declaration.consent, grant.consent)) {
      throw new EgressDenied("The declared consent does not match the consent-ledger entry.");
    }
    assertLive(grant.consent, this.#now());
    if (declaration.destination !== grant.destination) {
      throw new EgressDenied("The requested destination is outside the consent-ledger grant.");
    }
    if (declaration.payload_kind !== grant.payload_kind) {
      throw new EgressDenied("The requested payload class is outside the consent-ledger grant.");
    }
    if (normalizedOrigin(request.url) !== normalizedOrigin(grant.allowed_origin)) {
      throw new EgressDenied("The destination origin is outside the consent-ledger grant.");
    }
    if (declaration.destination === "telemetry" && request.init?.body != null) {
      assertTelemetryPayloadSafe(request.init.body);
    }

    return this.#transport(request.url, request.init);
  }
}

const PRIVATE_KEY = /(?:^|_)(?:absolute_)?path$|(?:^|_)(?:file_?name|filename)$|exif|gps|location|source_locator/i;
const ABSOLUTE_PATH = /(?:^|\s)(?:file:\/\/|\/[\w.-]|[a-z]:[\\/])/i;
const MEDIA_FILENAME = /(?:^|[\\/\s])[^\\/\s]+\.(?:avif|heic|heif|jpe?g|mov|mp4|m4v|png|raw|tiff?|webp)(?:$|\s)/i;

function inspectTelemetry(value: unknown, trail: string, seen: Set<object>): void {
  if (value == null || typeof value === "boolean" || typeof value === "number") return;
  if (typeof value === "string") {
    if (ABSOLUTE_PATH.test(value) || MEDIA_FILENAME.test(value)) {
      throw new EgressDenied(`Telemetry field ${trail} contains a local path or media filename.`);
    }
    return;
  }
  if (typeof value !== "object") {
    throw new EgressDenied(`Telemetry field ${trail} has an unsupported value type.`);
  }
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value) || value instanceof Blob) {
    throw new EgressDenied(`Telemetry field ${trail} contains opaque bytes.`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null) {
    throw new EgressDenied(`Telemetry field ${trail} has an unsupported payload container.`);
  }
  if (seen.has(value)) {
    throw new EgressDenied(`Telemetry field ${trail} contains a reference cycle.`);
  }
  seen.add(value);
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) inspectTelemetry(item, `${trail}[${index}]`, seen);
  } else {
    for (const [key, item] of Object.entries(value)) {
      if (PRIVATE_KEY.test(key)) {
        throw new EgressDenied(`Telemetry field ${trail}.${key} is private metadata.`);
      }
      inspectTelemetry(item, `${trail}.${key}`, seen);
    }
  }
  seen.delete(value);
}

/** Reject rather than redact: a caller must construct a deliberately safe payload. */
export function assertTelemetryPayloadSafe(payload: unknown): void {
  let decoded = payload;
  if (typeof payload === "string") {
    try {
      decoded = JSON.parse(payload) as unknown;
    } catch {
      // A plain error message is inspected as a string below.
    }
  }
  inspectTelemetry(decoded, "$", new Set());
}
