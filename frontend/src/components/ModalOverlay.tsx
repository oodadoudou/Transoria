import type { ComponentProps } from "react";
import { createPortal } from "react-dom";
import styles from "./ModalOverlay.module.css";

export function ModalOverlay({ className = "", ...props }: ComponentProps<"div">) {
  return createPortal(
    <div {...props} className={`${className} ${styles.viewport}`} />,
    document.body,
  );
}
