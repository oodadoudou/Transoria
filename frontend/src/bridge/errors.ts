import type { BridgeErrorPayload } from './types';

export class BridgeError extends Error {
  readonly code: string;
  readonly messageKey?: string;
  readonly details?: Record<string, unknown>;
  readonly retryable: boolean;

  constructor(payload: BridgeErrorPayload) {
    super(payload.message);
    this.name = 'BridgeError';
    this.code = payload.code;
    this.messageKey = payload.message_key;
    this.details = payload.details;
    this.retryable = payload.retryable;
  }

  static isBridgeError(error: unknown): error is BridgeError {
    return error instanceof BridgeError;
  }
}

export function isBridgeErrorPayload(value: unknown): value is BridgeErrorPayload {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.code === 'string' &&
    typeof candidate.message === 'string' &&
    typeof candidate.retryable === 'boolean'
  );
}
