import type { JobErrorCode } from "../../../contracts/codegen/generated/typescript/index.js";

export class RenderVideoError extends Error {
  readonly code: JobErrorCode;
  readonly retryable: boolean;

  constructor(code: JobErrorCode, message: string, retryable = false) {
    super(message);
    this.name = "RenderVideoError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function asRenderVideoError(error: unknown): RenderVideoError {
  if (error instanceof RenderVideoError) return error;
  return new RenderVideoError("internal_error", "The video renderer failed internally.", true);
}
