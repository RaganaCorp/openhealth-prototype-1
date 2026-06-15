import { useRef } from "react";
import type { MouseEvent } from "react";

/**
 * Overlay click-to-dismiss handlers that only fire when BOTH the press and the
 * release land on the overlay itself. Without this, a drag that starts inside the
 * modal and releases outside it (e.g. a slow click, or selecting text) dispatches
 * the `click` to the overlay — their nearest common ancestor — and closes the
 * modal unintentionally.
 *
 * Spread the returned props onto the overlay element:
 *   <div className="modal-overlay" {...useModalDismiss(onClose)}>
 */
export function useModalDismiss(onDismiss: () => void) {
  const pressedOnOverlay = useRef(false);

  return {
    onMouseDown(event: MouseEvent) {
      // True only when the press lands on the overlay itself, not a child.
      pressedOnOverlay.current = event.target === event.currentTarget;
    },
    onMouseUp(event: MouseEvent) {
      // Dismiss only when BOTH the press and the release are on the overlay, so a
      // drag that starts or ends inside the panel never closes it.
      const releasedOnOverlay = event.target === event.currentTarget;
      if (pressedOnOverlay.current && releasedOnOverlay) {
        onDismiss();
      }
      pressedOnOverlay.current = false;
    },
  };
}
