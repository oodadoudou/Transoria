import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

class TestResizeObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  configurable: true,
  value: window.ResizeObserver ?? TestResizeObserver,
});

if (!HTMLElement.prototype.scrollTo) {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    writable: true,
    configurable: true,
    value(options: ScrollToOptions) {
      if (typeof options.top === "number") {
        this.scrollTop = options.top;
      }
    },
  });
}
