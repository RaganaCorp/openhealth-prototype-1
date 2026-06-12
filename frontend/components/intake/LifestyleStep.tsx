"use client";

import type { SocialHistoryDraft } from "./types";

type LifestyleStepProps = {
  patientName: string;
  value: SocialHistoryDraft;
  onChange: (next: SocialHistoryDraft) => void;
};

type Option<T extends string> = { value: T; label: string };

function ChipGroup<T extends string>({
  legend,
  options,
  selected,
  onSelect,
}: {
  legend: string;
  options: Option<T>[];
  selected: T | "";
  onSelect: (value: T | "") => void;
}) {
  return (
    <fieldset className="field-group">
      <legend className="field-label">{legend}</legend>
      <div className="flex flex-wrap gap-2">
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
      </div>
    </fieldset>
  );
}

export function LifestyleStep({ patientName, value, onChange }: LifestyleStepProps) {
  function patch(updates: Partial<SocialHistoryDraft>) {
    onChange({ ...value, ...updates });
  }

  const displayName = patientName.trim() || "this patient";

  return (
    <div className="space-y-5">
      <div className="border-b border-border/80 pb-4">
        <p className="eyebrow">Lifestyle &amp; social history</p>
        <h2 className="text-2xl font-semibold text-text-primary">Habits that affect {displayName}&rsquo;s care</h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          These can change clinical recommendations. All optional — share what&rsquo;s relevant.
        </p>
      </div>

      {/* Tobacco */}
      <div className="space-y-3">
        <ChipGroup
          legend="Smoking / tobacco"
          onSelect={(v) => patch({ tobaccoStatus: v })}
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
      <div className="space-y-3">
        <ChipGroup
          legend="Alcohol use"
          onSelect={(v) => patch({ alcoholStatus: v })}
          options={[
            { value: "never", label: "Never" },
            { value: "occasional", label: "Occasional" },
            { value: "moderate", label: "Moderate" },
            { value: "heavy", label: "Heavy" },
          ]}
          selected={value.alcoholStatus}
        />
        {value.alcoholStatus && value.alcoholStatus !== "never" ? (
          <label className="flex items-center gap-2">
            <input
              className="field-input w-28"
              inputMode="numeric"
              min={0}
              onChange={(event) => patch({ alcoholDrinksPerWeek: event.target.value })}
              placeholder="0"
              type="number"
              value={value.alcoholDrinksPerWeek}
            />
            <span className="text-sm text-text-secondary">drinks / week</span>
          </label>
        ) : null}
      </div>

      {/* Recreational drugs */}
      <div className="space-y-3">
        <ChipGroup
          legend="Recreational drug use"
          onSelect={(v) => patch({ drugStatus: v })}
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
          onSelect={(v) => patch({ sexualActivityStatus: v })}
          options={[
            { value: "not_active", label: "Not active" },
            { value: "active", label: "Active" },
          ]}
          selected={value.sexualActivityStatus}
        />
        {value.sexualActivityStatus === "active" ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="field-group">
              <span className="field-label">Partners</span>
              <input
                className="field-input"
                onChange={(event) => patch({ sexualPartners: event.target.value })}
                placeholder="e.g. 1, multiple"
                value={value.sexualPartners}
              />
            </label>
            <label className="field-group">
              <span className="field-label">Partner gender(s)</span>
              <input
                className="field-input"
                onChange={(event) => patch({ sexualPartnerGenders: event.target.value })}
                placeholder="e.g. men, women"
                value={value.sexualPartnerGenders}
              />
            </label>
            <fieldset className="field-group sm:col-span-2">
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
