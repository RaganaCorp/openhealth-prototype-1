"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Renders modal markup into <body> so a `position: fixed` overlay covers the whole
 * viewport. Without this, an ancestor with backdrop-filter/filter/transform becomes
 * the containing block for fixed children and traps the overlay inside that element
 * (which squished the dialogs opened from inside a workspace card).
 */
export function ModalPortal({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  if (!mounted) {
    return null;
  }
  return createPortal(children, document.body);
}
