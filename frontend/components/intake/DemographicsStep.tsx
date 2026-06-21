"use client";

import { useState } from "react";

import { InfoIcon } from "@/components/icons";
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
  // When embedded in the profile-edit modal, the intake-style header is hidden
  // (the modal's tabs already identify the section).
  embedded?: boolean;
};

const SEX_OPTIONS: { value: SexAssignedAtBirth; label: string }[] = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "intersex", label: "Intersex" },
  { value: "undisclosed", label: "Prefer not to say" },
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function DemographicsStep({ patientName, value, onChange, embedded }: DemographicsStepProps) {
  const [whyOpen, setWhyOpen] = useState(false);

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

  // Reference figures (rough adult medians) used only as placeholders, to hint the
  // expected format once sex is known. cm/kg derive from the imperial values.
  const sexDefaults =
    value.sex === "male"
      ? { feet: 5, inches: 8, lbs: 172 }
      : value.sex === "female"
        ? { feet: 5, inches: 3, lbs: 155 }
        : null;
  const heightFeetPlaceholder = sexDefaults ? String(sexDefaults.feet) : "5";
  const heightInchesPlaceholder = sexDefaults ? String(sexDefaults.inches) : "9";
  const heightCmPlaceholder = sexDefaults
    ? String(Math.round(feetInchesToCm(sexDefaults.feet, sexDefaults.inches)))
    : "175";
  const weightPlaceholder = sexDefaults
    ? value.weightUnit === "kg"
      ? String(Math.round(lbsToKg(sexDefaults.lbs)))
      : String(sexDefaults.lbs)
    : "0";

  return (
    <div className="space-y-5">
      {embedded ? null : (
        <div className="border-b border-border/80 pb-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="eyebrow">Demographics</p>
              <h2 className="text-2xl font-semibold text-text-primary">Tell us about {patientName || "this patient"}</h2>
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
            Everything here is optional — add what you know and skip the rest.
          </p>
          {whyOpen ? (
            <div className="mt-3 animate-fade-up rounded-xl border border-border/70 bg-surface-elevated/60 p-3.5 text-sm leading-6 text-text-secondary">
              These details give the local AI medical context for the record. Age, sex, and body measurements shift what counts
              as normal — lab reference ranges, medication dosing, and risk factors are all read against them — so sharing what
              you know leads to more accurate, grounded answers. Date of birth also helps place each uploaded document at the
              right point in the timeline. It stays on your computer and you can change it anytime.
            </div>
          ) : null}
        </div>
      )}

      <label className="field-group">
        <span className="field-label">Date of birth</span>
        <input
          autoFocus
          className={`field-input ${value.dob ? "is-filled" : ""}`}
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
        <span className="field-label">Gender identity</span>
        <input
          className={`field-input ${value.genderIdentity.trim() ? "is-filled" : ""}`}
          onChange={(event) => patch({ genderIdentity: event.target.value })}
          placeholder="How do they identify?"
          value={value.genderIdentity}
        />
      </label>

      <div className="grid gap-5 sm:grid-cols-2">
        <div className="field-group">
          <span className="field-label">Height</span>
          <div className="flex flex-wrap items-center gap-2">
            {value.heightUnit === "ftin" ? (
              <>
                <label className="flex items-center gap-1.5">
                  <input
                    className="field-input field-num-xs"
                    inputMode="numeric"
                    min={0}
                    onChange={(event) => patch({ heightFeet: event.target.value })}
                    placeholder={heightFeetPlaceholder}
                    type="number"
                    value={value.heightFeet}
                  />
                  <span className="text-sm text-text-secondary">ft</span>
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    className="field-input field-num-xs"
                    inputMode="numeric"
                    max={11}
                    min={0}
                    onChange={(event) => patch({ heightInches: event.target.value })}
                    placeholder={heightInchesPlaceholder}
                    type="number"
                    value={value.heightInches}
                  />
                  <span className="text-sm text-text-secondary">in</span>
                </label>
              </>
            ) : (
              <input
                aria-label="Height in cm"
                className="field-input field-num"
                inputMode="numeric"
                min={0}
                onChange={(event) => patch({ heightCm: event.target.value })}
                placeholder={heightCmPlaceholder}
                type="number"
                value={value.heightCm}
              />
            )}
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
        </div>

        <div className="field-group">
          <span className="field-label">Weight</span>
          <div className="flex flex-wrap items-center gap-2">
            <input
              aria-label={`Weight in ${value.weightUnit}`}
              className="field-input field-num"
              inputMode="numeric"
              min={0}
              onChange={(event) => patch({ weightValue: event.target.value })}
              placeholder={weightPlaceholder}
              type="number"
              value={value.weightValue}
            />
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
        </div>
      </div>
    </div>
  );
}
