"use client";

import { useState, type ReactNode } from "react";

import { InfoIcon } from "@/components/icons";
import type { SocialHistoryDraft } from "./types";

type LifestyleStepProps = {
  patientName: string;
  value: SocialHistoryDraft;
  onChange: (next: SocialHistoryDraft) => void;
  // Hides the intake-style header when embedded in the profile-edit modal.
  embedded?: boolean;
};

type Option<T extends string> = { value: T; label: string };

function ChipGroup<T extends string>({
  legend,
  options,
  selected,
  onSelect,
  trailing,
}: {
  legend: string;
  options: Option<T>[];
  selected: T | "";
  onSelect: (value: T | "") => void;
  // Optional content rendered inline after the chips (e.g. a dependent input).
  trailing?: ReactNode;
}) {
  return (
    <fieldset className="field-group">
      <legend className="field-label">{legend}</legend>
      <div className="flex flex-wrap items-center gap-2">
        {options.map((option) => {
          const active = selected === option.value;
          return (
            <button
              aria-pressed={active}
              className={`chip-toggle ${active ? "active" : ""}`}
              key={option.value}
              // Re-selecting the active chip clears it (every field is optional).
              onClick={() => onSelect(active ? "" : option.value)}
              type="button"
            >
              {option.label}
            </button>
          );
        })}
        {trailing}
      </div>
    </fieldset>
  );
}

export function LifestyleStep({ patientName, value, onChange, embedded }: LifestyleStepProps) {
  const [whyOpen, setWhyOpen] = useState(false);

  function patch(updates: Partial<SocialHistoryDraft>) {
    onChange({ ...value, ...updates });
  }

  // When a status changes to a value that hides its dependent inputs, clear those
  // inputs so a stale value (e.g. partners after switching to "not active") is
  // never retained or saved.
  function setTobaccoStatus(status: SocialHistoryDraft["tobaccoStatus"]) {
    const keepsDetails = status === "current" || status === "former";
    patch({ tobaccoStatus: status, ...(keepsDetails ? {} : { tobaccoDetails: "" }) });
  }

  function setAlcoholStatus(status: SocialHistoryDraft["alcoholStatus"]) {
    const keepsDrinks = status !== "" && status !== "never";
    patch({ alcoholStatus: status, ...(keepsDrinks ? {} : { alcoholDrinksPerWeek: "" }) });
  }

  function setDrugStatus(status: SocialHistoryDraft["drugStatus"]) {
    const keepsSubstances = status === "current" || status === "former";
    patch({ drugStatus: status, ...(keepsSubstances ? {} : { drugSubstances: "" }) });
  }

  function setSexualActivityStatus(status: SocialHistoryDraft["sexualActivityStatus"]) {
    const keepsDetails = status === "active";
    patch({
      sexualActivityStatus: status,
      ...(keepsDetails ? {} : { sexualPartnerGenders: "", sexualProtection: "" }),
    });
  }

  const displayName = patientName.trim() || "this patient";

  return (
    <div className="space-y-5">
      {embedded ? null : (
        <div className="border-b border-border/80 pb-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="eyebrow">Lifestyle &amp; social history</p>
              <h2 className="text-2xl font-semibold text-text-primary">Habits that affect {displayName}&rsquo;s care</h2>
            </div>
            <button
              aria-expanded={whyOpen}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg px-1.5 py-1 text-xs text-text-muted transition-colors hover:text-primary"
              onClick={() => setWhyOpen((open) => !open)}
              type="button"
            >
              <InfoIcon size={14} />
              Why we&apos;re asking
            </button>
          </div>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            These can change clinical recommendations. All optional — share what&rsquo;s relevant.
          </p>
          {whyOpen ? (
            <div className="mt-3 animate-fade-up rounded-xl border border-border/70 bg-surface-elevated/60 p-3.5 text-sm leading-6 text-text-secondary">
              Lifestyle and social factors change how the record should be read — tobacco, alcohol, and drug use affect risk,
              likely diagnoses, medication choices, and screening guidance. Sharing what&rsquo;s relevant helps the local AI give
              safer, more grounded answers. It stays on your computer and you can change it anytime.
            </div>
          ) : null}
        </div>
      )}

      {/* Tobacco */}
      <div className="space-y-3">
        <ChipGroup
          legend="Smoking / tobacco"
          onSelect={setTobaccoStatus}
          options={[
            { value: "never", label: "Never" },
            { value: "former", label: "Former" },
            { value: "current", label: "Current" },
          ]}
          selected={value.tobaccoStatus}
        />
        {value.tobaccoStatus === "current" || value.tobaccoStatus === "former" ? (
          <input
            aria-label="Tobacco details"
            className="field-input"
            onChange={(event) => patch({ tobaccoDetails: event.target.value })}
            placeholder="Details — e.g. 1 pack/day for 10 years, vaping"
            value={value.tobaccoDetails}
          />
        ) : null}
      </div>

      {/* Alcohol */}
      <ChipGroup
        legend="Alcohol use"
        onSelect={setAlcoholStatus}
        options={[
          { value: "never", label: "Never" },
          { value: "occasional", label: "Occasional" },
          { value: "moderate", label: "Moderate" },
          { value: "heavy", label: "Heavy" },
        ]}
        selected={value.alcoholStatus}
        trailing={
          value.alcoholStatus && value.alcoholStatus !== "never" ? (
            <label className="ml-1 flex items-center gap-2">
              <input
                className="field-input field-num"
                inputMode="numeric"
                min={0}
                onChange={(event) => patch({ alcoholDrinksPerWeek: event.target.value })}
                placeholder="0"
                type="number"
                value={value.alcoholDrinksPerWeek}
              />
              <span className="text-sm text-text-secondary">drinks / week</span>
            </label>
          ) : null
        }
      />

      {/* Recreational drugs */}
      <div className="space-y-3">
        <ChipGroup
          legend="Recreational drug use"
          onSelect={setDrugStatus}
          options={[
            { value: "never", label: "Never" },
            { value: "former", label: "Former" },
            { value: "current", label: "Current" },
          ]}
          selected={value.drugStatus}
        />
        {value.drugStatus === "current" || value.drugStatus === "former" ? (
          <input
            aria-label="Substances"
            className="field-input"
            onChange={(event) => patch({ drugSubstances: event.target.value })}
            placeholder="Substances — e.g. cannabis, stimulants"
            value={value.drugSubstances}
          />
        ) : null}
      </div>

      {/* Sexual history */}
      <div className="space-y-3">
        <ChipGroup
          legend="Sexual activity"
          onSelect={setSexualActivityStatus}
          options={[
            { value: "not_active", label: "Not active" },
            { value: "active", label: "Active" },
          ]}
          selected={value.sexualActivityStatus}
        />
        {value.sexualActivityStatus === "active" ? (
          <div className="space-y-3">
            <label className="field-group">
              <span className="field-label">Partner gender(s)</span>
              <input
                className="field-input sm:max-w-xs"
                onChange={(event) => patch({ sexualPartnerGenders: event.target.value })}
                placeholder="e.g. men, women"
                value={value.sexualPartnerGenders}
              />
            </label>
            <fieldset className="field-group">
              <legend className="field-label">Protection / contraception</legend>
              <div className="flex flex-wrap gap-2">
                {(["always", "sometimes", "never"] as const).map((option) => {
                  const active = value.sexualProtection === option;
                  return (
                    <button
                      aria-pressed={active}
                      className={`chip-toggle ${active ? "active" : ""}`}
                      key={option}
                      onClick={() => patch({ sexualProtection: active ? "" : option })}
                      type="button"
                    >
                      {option[0].toUpperCase() + option.slice(1)}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          </div>
        ) : null}
      </div>
    </div>
  );
}
