import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsToolbar } from "./SettingsToolbar";

describe("SettingsToolbar", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not reset when the user cancels the confirmation", async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <SettingsToolbar
        saveState="idle"
        lastError={null}
        onSave={vi.fn()}
        onReset={onReset}
      />,
    );

    await user.click(screen.getByRole("button", { name: "恢复默认值" }));

    expect(window.confirm).toHaveBeenCalledWith(
      "确定要将本模块设置恢复为默认值吗？当前设置会被丢弃。",
    );
    expect(onReset).not.toHaveBeenCalled();
  });

  it("resets after the user confirms", async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <SettingsToolbar
        saveState="idle"
        lastError={null}
        onSave={vi.fn()}
        onReset={onReset}
      />,
    );

    await user.click(screen.getByRole("button", { name: "恢复默认值" }));

    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("keeps reset disabled while settings are saving", () => {
    render(
      <SettingsToolbar
        saveState="saving"
        lastError={null}
        onSave={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "恢复默认值" })).toBeDisabled();
  });
});
