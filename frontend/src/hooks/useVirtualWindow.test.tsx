import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { useVirtualWindow } from "./useVirtualWindow";

function Harness() {
  const virtual = useVirtualWindow({ count: 100, rowHeight: 10 });
  return (
    <div>
      <div data-testid="scroll-top">{virtual.scrollTop}</div>
      <div data-testid="start-index">{virtual.startIndex}</div>
      <div
        ref={virtual.containerRef}
        onScroll={virtual.handleScroll}
        style={{ height: 50, overflowY: "auto" }}
      />
      <button type="button" onClick={() => virtual.scrollToOffset(125)}>
        offset
      </button>
      <button type="button" onClick={() => virtual.scrollToIndex(7)}>
        index
      </button>
    </div>
  );
}

describe("useVirtualWindow", () => {
  it("tracks direct offset scrolling", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "offset" }));

    expect(screen.getByTestId("scroll-top")).toHaveTextContent("125");
    expect(screen.getByTestId("start-index")).toHaveTextContent("4");
  });

  it("scrolls to a row index", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "index" }));

    expect(screen.getByTestId("scroll-top")).toHaveTextContent("70");
  });
});
