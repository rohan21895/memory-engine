import type { JobErrorCode } from "../../../contracts/codegen/generated/typescript/index.js";

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
  return new RenderPrintError("internal_error", "The print renderer failed internally.", true);
}
