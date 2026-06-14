"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";

import { GearIcon } from "@/components/icons";
import { SettingsModal } from "@/components/SettingsModal";

export function Header() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const isPatientRoute = pathname.startsWith("/patient/");

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/88 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1560px] items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <div>
            <div className="flex items-baseline gap-3">
              <span className="text-xl font-semibold tracking-tight text-text-primary">OpenHealth</span>
              <span className="rounded-full bg-primary-light px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.24em] text-primary">v1</span>
            </div>
            <p className="mt-1 text-sm text-text-secondary">
              {isPatientRoute ? "Grounded chat and summaries in one workspace" : "Create patient workspaces and ingest records locally"}
            </p>
          </div>
          <button aria-label="Open settings" className="icon-button" onClick={() => setOpen(true)} type="button">
            <GearIcon />
          </button>
        </div>
      </header>
      <SettingsModal onClose={() => setOpen(false)} open={open} />
    </>
  );
}
