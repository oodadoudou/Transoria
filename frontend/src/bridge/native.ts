import { BridgeError } from "./errors";
import { getTransport } from "./transport";
import type { DialogPathResult, GlossaryFileResult } from "./types";

interface NativeApi {
  choose_directory?: (payload?: {
    initial_path?: string;
  }) => Promise<DialogPathResult> | DialogPathResult;
  choose_file?: (payload?: {
    initial_path?: string;
    extensions?: string[];
  }) => Promise<DialogPathResult> | DialogPathResult;
  save_file?: (payload?: {
    default_filename?: string;
    extensions?: string[];
  }) => Promise<DialogPathResult> | DialogPathResult;
  open_directory?: (payload: {
    path: string;
  }) => Promise<Record<string, unknown>> | Record<string, unknown>;
  reveal_file?: (payload: {
    path: string;
  }) => Promise<Record<string, unknown>> | Record<string, unknown>;
}

declare global {
  interface Window {
    pywebview?: {
      api?: NativeApi;
    };
  }
}

function nativeApi(): NativeApi {
  const api = window.pywebview?.api;
  if (!api) {
    throw new BridgeError({
      code: "bridge.io_error",
      message: "Native desktop API is unavailable in browser mode.",
      retryable: false,
    });
  }
  return api;
}

export const nativeDialogs = {
  async chooseDirectory(initialPath?: string): Promise<DialogPathResult> {
    const handler = nativeApi().choose_directory;
    if (!handler) {
      throw missing("choose_directory");
    }
    return handler({ initial_path: initialPath });
  },

  async chooseFile(
    initialPath: string | undefined,
    extensions: string[],
  ): Promise<DialogPathResult> {
    const handler = nativeApi().choose_file;
    if (!handler) {
      throw missing("choose_file");
    }
    return handler({ initial_path: initialPath, extensions });
  },

  async chooseGlossaryFile(
    initialPath: string | undefined,
    extensions: string[],
  ): Promise<GlossaryFileResult> {
    const result = await this.chooseFile(initialPath, extensions);
    return {
      path: result.path,
      format: inferGlossaryFormat(result.path),
    };
  },

  async chooseSavePath(
    defaultFilename: string,
    extensions: string[],
  ): Promise<DialogPathResult> {
    const handler = nativeApi().save_file;
    if (!handler) {
      throw missing("save_file");
    }
    return handler({ default_filename: defaultFilename, extensions });
  },

  async openDirectory(path: string): Promise<Record<string, never>> {
    const handler = optionalNativeApi()?.open_directory;
    if (!handler) {
      await getTransport().call("dialogs.open_directory", { path });
      return {};
    }
    await handler({ path });
    return {};
  },

  async revealFile(path: string): Promise<Record<string, never>> {
    const handler = optionalNativeApi()?.reveal_file;
    if (!handler) {
      await getTransport().call("dialogs.reveal_file", { path });
      return {};
    }
    await handler({ path });
    return {};
  },
};

function missing(method: string): BridgeError {
  return new BridgeError({
    code: "bridge.not_found",
    message: `Native method not registered: ${method}`,
    retryable: false,
  });
}

function optionalNativeApi(): NativeApi | null {
  return window.pywebview?.api ?? null;
}

function inferGlossaryFormat(path: string | null): "xlsx" | "json" | null {
  if (!path) return null;
  const lowered = path.toLowerCase();
  if (lowered.endsWith(".xlsx")) return "xlsx";
  if (lowered.endsWith(".json")) return "json";
  return null;
}
