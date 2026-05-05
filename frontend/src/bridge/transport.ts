import { BridgeError, isBridgeErrorPayload } from "./errors";

export interface BridgeTransport {
  call<TResponse>(method: string, payload: unknown): Promise<TResponse>;
  isConnected(): boolean;
}

export class HttpTransport implements BridgeTransport {
  private readonly baseUrl: string;

  constructor(baseUrl = "/api") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  isConnected(): boolean {
    return true;
  }

  async call<TResponse>(method: string, payload: unknown): Promise<TResponse> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      });
    } catch (error) {
      const rawMessage =
        error instanceof Error ? error.message : String(error || "");
      const pageUrl =
        typeof window !== "undefined" ? window.location.href : undefined;
      const message =
        rawMessage === "Failed to fetch"
          ? "network error: Failed to fetch. The local Transoria bridge is unavailable; restart Transoria, and make sure you launched Transoria.exe instead of opening frontend files directly."
          : `network error: ${rawMessage || "request failed"}`;
      throw new BridgeError({
        code: "bridge.io_error",
        message,
        retryable: true,
        details: { method, base_url: this.baseUrl, page_url: pageUrl },
      });
    }

    const text = await response.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      throw new BridgeError({
        code: "bridge.io_error",
        message: `non-JSON response (${response.status}): ${text.slice(0, 200)}`,
        retryable: false,
        details: { method, status: response.status },
      });
    }

    if (!response.ok) {
      if (isBridgeErrorPayload(body)) {
        throw new BridgeError(body);
      }
      throw new BridgeError({
        code: "bridge.io_error",
        message: `HTTP ${response.status}`,
        retryable: response.status >= 500,
        details: { method, status: response.status, body },
      });
    }

    return body as TResponse;
  }
}

let activeTransport: BridgeTransport = new HttpTransport();

export function getTransport(): BridgeTransport {
  return activeTransport;
}

export function setTransport(transport: BridgeTransport): void {
  activeTransport = transport;
}

export function resetTransport(): void {
  activeTransport = new HttpTransport();
}
