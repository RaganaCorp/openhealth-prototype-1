"use client";

import type { SexAssignedAtBirth } from "@/lib/api";
import {
  cmToFeetInches,
  feetInchesToCm,
  kgToLbs,
  lbsToKg,
  round1,
} from "@/lib/units";

import type { DemographicsDraft } from "./types";

type DemographicsStepProps = {
  patientName: string;
  value: DemographicsDraft;
  onChange: (next: DemographicsDraft) => void;
};

const SEX_OPTIONS: { value: SexAssignedAtBirth; label: string }[] = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "intersex", label: "Intersex" },
  { value: "undisclosed", label: "Prefer not to say" },
];

// Weight slider bounds, expressed per display unit.
const WEIGHT_RANGE = {
  lbs: { min: 1, max: 660, step: 1 },
  kg: { min: 1, max: 300, step: 1 },
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function DemographicsStep({ patientName, value, onChange }: DemographicsStepProps) {
  function patch(updates: Partial<DemographicsDraft>) {
    onChange({ ...value, ...updates });
  }

  function setHeightUnit(unit: "ftin" | "cm") {
    if (unit === value.heightUnit) {
      return;
    }
    if (unit === "cm") {
      // Convert current ft/in entry into a cm value so the number carries over.
      const feet = Number(value.heightFeet) || 0;
      const inches = Number(value.heightInches) || 0;
      const cm = feet > 0 || inches > 0 ? String(Math.round(feetInchesToCm(feet, inches))) : "";
      patch({ heightUnit: "cm", heightCm: cm });
    } else {
      const cm = Number(value.heightCm) || 0;
      if (cm > 0) {
        const { feet, inches } = cmToFeetInches(cm);
        patch({ heightUnit: "ftin", heightFeet: String(feet), heightInches: String(inches) });
      } else {
        patch({ heightUnit: "ftin" });
      }
    }
  }

  function setWeightUnit(unit: "lbs" | "kg") {
    if (unit === value.weightUnit) {
      return;
    }
    const current = Number(value.weightValue);
    let nextValue = "";
    if (Number.isFinite(current) && current > 0) {
      nextValue = unit === "kg" ? String(round1(lbsToKg(current))) : String(Math.round(kgToLbs(current)));
    }
    patch({ weightUnit: unit, weightValue: nextValue });
  }

  const weightRange = WEIGHT_RANGE[value.weightUnit];
  const weightNumber = Number(value.weightValue);
  const sliderValue = Number.isFinite(weightNumber) && weightNumber > 0 ? weightNumber : weightRange.min;

  return (
    <div className="space-y-5">
      <div className="border-b border-border/80 pb-4">
        <p className="eyebrow">Demographics</p>
        <h2 className="text-2xl font-semibold text-text-primary">Tell us about {patientName || "this patient"}</h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Everything here is optional — add what you know and skip the rest.
        </p>
      </div>

      <label className="field-group">
        <span className="field-label">Date of birth</span>
        <input
          autoFocus
          className="field-input"
          max={todayIso()}
          onChange={(event) => patch({ dob: event.target.value })}
          type="date"
          value={value.dob}
        />
      </label>

      <fieldset className="field-group">
        <legend className="field-label">Sex assigned at birth</legend>
        <div className="flex flex-wrap gap-2">
          {SEX_OPTIONS.map((option) => {
            const active = value.sex === option.value;
            return (
              <button
                aria-pressed={active}
                className={`chip-toggle ${active ? "active" : ""}`}
                key={option.value}
                onClick={() => patch({ sex: active ? "" : option.value })}
                type="button"
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <label className="field-group">
        <span className="field-label">Gender identity (optional)</span>
        <input
          className="field-input"
          onChange={(event) => patch({ genderIdentity: event.target.value })}
          placeholder="How do they identify?"
          value={value.genderIdentity}
        />
      </label>

      <div className="field-group">
        <div className="flex items-center justify-between">
          <span className="field-label">Height (optional)</span>
          <div className="unit-toggle" role="group" aria-label="Height unit">
            <button
              aria-pressed={value.heightUnit === "ftin"}
              className={`unit-toggle-button ${value.heightUnit === "ftin" ? "active" : ""}`}
              onClick={() => setHeightUnit("ftin")}
              type="button"
            >
              ft / in
            </button>
            <button
              aria-pressed={value.heightUnit === "cm"}
              className={`unit-toggle-button ${value.heightUnit === "cm" ? "active" : ""}`}
              onClick={() => setHeightUnit("cm")}
              type="button"
            >
              cm
            </button>
          </div>
        </div>
        {value.heightUnit === "ftin" ? (
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center gap-2">
              <input
                className="field-input"
                inputMode="numeric"
                min={0}
                onChange={(event) => patch({ heightFeet: event.target.value })}
                placeholder="5"
                type="number"
                value={value.heightFeet}
              />
              <span className="text-sm text-text-secondary">ft</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                className="field-input"
                inputMode="numeric"
                max={11}
                min={0}
                onChange={(event) => patch({ heightInches: event.target.value })}
                placeholder="9"
                type="number"
                value={value.heightInches}
              />
              <span className="text-sm text-text-secondary">in</span>
            </label>
          </div>
        ) : (
          <label className="flex items-center gap-2">
            <input
              className="field-input"
              inputMode="numeric"
              min={0}
              onChange={(event) => patch({ heightCm: event.target.value })}
              placeholder="175"
              type="number"
              value={value.heightCm}
            />
            <span className="text-sm text-text-secondary">cm</span>
          </label>
        )}
      </div>

      <div className="field-group">
        <div className="flex items-center justify-between">
          <span className="field-label">Weight (optional)</span>
          <div className="unit-toggle" role="group" aria-label="Weight unit">
            <button
              aria-pressed={value.weightUnit === "lbs"}
              className={`unit-toggle-button ${value.weightUnit === "lbs" ? "active" : ""}`}
              onClick={() => setWeightUnit("lbs")}
              type="button"
            >
              lbs
            </button>
            <button
              aria-pressed={value.weightUnit === "kg"}
              className={`unit-toggle-button ${value.weightUnit === "kg" ? "active" : ""}`}
              onClick={() => setWeightUnit("kg")}
              type="button"
            >
              kg
            </button>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <input
            aria-label={`Weight in ${value.weightUnit}`}
            className="flex-1"
            max={weightRange.max}
            min={weightRange.min}
            onChange={(event) => patch({ weightValue: event.target.value })}
            step={weightRange.step}
            type="range"
            value={sliderValue}
          />
          <div className="flex items-center gap-2">
            <input
              className="field-input w-24"
              inputMode="numeric"
              min={0}
              onChange={(event) => patch({ weightValue: event.target.value })}
              placeholder="0"
              type="number"
              value={value.weightValue}
            />
            <span className="text-sm text-text-secondary">{value.weightUnit}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
