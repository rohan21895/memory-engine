import type { JobErrorCode } from "../../../contracts/codegen/generated/typescript/index.js";

import { PublicationBlocked } from "./clearance.js";

export class RenderPrintError extends Error {
  readonly code: JobErrorCode;
  readonly retryable: boolean;

  constructor(code: JobErrorCode, message: string, retryable = false) {
    super(message);
    this.name = "RenderPrintError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function asRenderPrintError(error: unknown): RenderPrintError {
  if (error instanceof RenderPrintError) return error;
  if (error instanceof PublicationBlocked) {
    // NOT `internal_error`, and NOT retryable.
    //
    // `internal_error` is retryable in this worker, and a safety refusal that
    // an orchestrator retries is a safety refusal that eventually runs on a
    // different day with a different manifest. Retrying this job with these
    // params gets the same denial by construction; the fix is to obtain a
    // clearance, which changes `params_digest` and is therefore a different
    // job.
    //
    // `validation_failed` is the closest existing code and it is not a good
    // fit: it reads as "the AlbumSpec is malformed" and this is "nobody checked
    // what is in the album". `JobError.code` has no safety member --
    // contracts/schemas/job-spec.schema.json, and adding one is a
    // cross-boundary change that needs Codex's sign-off rather than a
    // unilateral edit. Raised in the branch report. The denial code is carried
    // verbatim in the message so the distinction is not lost in the meantime.
    return new RenderPrintError(
      "validation_failed",
      `Sensitive-content clearance denied this print export [${error.code}]: ${error.detail}`,
      false,
    );
  }
  return new RenderPrintError("internal_error", "The print renderer failed internally.", true);
}
