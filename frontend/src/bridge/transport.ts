import { BridgeError, isBridgeErrorPayload } from './errors';

export interface BridgeTransport {
  call<TResponse>(method: string, payload: unknown): Promise<TResponse>;
  isConnected(): boolean;
}

interface PywebviewBridge {
  api?: Record<string, (payload: unknown) => Promise<unknown>>;
}

declare global {
  interface Window {
    pywebview?: PywebviewBridge;
  }
}

class PywebviewTransport implements BridgeTransport {
  isConnected(): boolean {
    return typeof window !== 'undefined' && Boolean(window.pywebview?.api);
  }

  async call<TResponse>(method: string, payload: unknown): Promise<TResponse> {
    const api = window.pywebview?.api;
    if (!api) {
      throw new BridgeError({
        code: 'bridge.io_error',
        message: 'pywebview bridge is not available',
        retryable: false,
      });
    }
    const handler = api[method.replace(/\./g, '__')];
    if (!handler) {
      throw new BridgeError({
        code: 'bridge.not_found',
        message: `Bridge method not registered: ${method}`,
        retryable: false,
      });
    }
    try {
      const result = await handler(payload);
      return result as TResponse;
    } catch (error) {
      if (isBridgeErrorPayload(error)) {
        throw new BridgeError(error);
      }
      throw new BridgeError({
        code: 'bridge.io_error',
        message: error instanceof Error ? error.message : String(error),
        retryable: true,
      });
    }
  }
}

class DisconnectedTransport implements BridgeTransport {
  isConnected(): boolean {
    return false;
  }

  async call<TResponse>(method: string, _payload: unknown): Promise<TResponse> {
    throw new BridgeError({
      code: 'bridge.io_error',
      message:
        'Backend bridge is not connected. Run the desktop shell to enable ' +
        `${method}.`,
      retryable: false,
    });
  }
}

let activeTransport: BridgeTransport = pickTransport();

function pickTransport(): BridgeTransport {
  if (typeof window !== 'undefined' && window.pywebview?.api) {
    return new PywebviewTransport();
  }
  return new DisconnectedTransport();
}

export function getTransport(): BridgeTransport {
  return activeTransport;
}

export function setTransport(transport: BridgeTransport): void {
  activeTransport = transport;
}

export function resetTransport(): void {
  activeTransport = pickTransport();
}
