import type { PatientCondition, PatientIntake, SexAssignedAtBirth } from "@/lib/api";
import { feetInchesToCm, round1 } from "@/lib/units";

// Display-oriented draft for the demographics step. Raw fields (including the
// chosen display unit) live here so that navigating Back and forward never loses
// what the user typed. Canonical cm/kg are derived only when building the payload.
export type DemographicsDraft = {
  dob: string; // "YYYY-MM-DD" or ""
  sex: SexAssignedAtBirth | "";
  genderIdentity: string;
  heightUnit: "ftin" | "cm";
  heightFeet: string;
  heightInches: string;
  heightCm: string;
  weightUnit: "lbs" | "kg";
  weightValue: string; // in the currently selected weightUnit
};

export const emptyDemographics: DemographicsDraft = {
  dob: "",
  sex: "",
  genderIdentity: "",
  heightUnit: "ftin",
  heightFeet: "",
  heightInches: "",
  heightCm: "",
  weightUnit: "lbs",
  weightValue: "",
};

function toNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

export function draftHeightToCm(d: DemographicsDraft): number | null {
  if (d.heightUnit === "cm") {
    const cm = toNumber(d.heightCm);
    return cm && cm > 0 ? round1(cm) : null;
  }
  const feet = toNumber(d.heightFeet) ?? 0;
  const inches = toNumber(d.heightInches) ?? 0;
  if (feet <= 0 && inches <= 0) {
    return null;
  }
  return round1(feetInchesToCm(feet, inches));
}

export function draftWeightToKg(d: DemographicsDraft): number | null {
  const value = toNumber(d.weightValue);
  if (!value || value <= 0) {
    return null;
  }
  // weightValue is stored in the active unit; the step converts on toggle, but we
  // re-derive here defensively from the unit it is currently displayed in.
  if (d.weightUnit === "kg") {
    return round1(value);
  }
  return round1(value * 0.45359237);
}

export function buildIntakePayload(
  name: string,
  demographics: DemographicsDraft,
  conditions: PatientCondition[],
): PatientIntake {
  return {
    name: name.trim(),
    dob: demographics.dob || null,
    sex_assigned_at_birth: demographics.sex || null,
    gender_identity: demographics.genderIdentity.trim() || null,
    height_cm: draftHeightToCm(demographics),
    weight_kg: draftWeightToKg(demographics),
    conditions,
  };
}

export function demographicsTouched(d: DemographicsDraft): boolean {
  return (
    d.dob !== "" ||
    d.sex !== "" ||
    d.genderIdentity.trim() !== "" ||
    d.heightFeet !== "" ||
    d.heightInches !== "" ||
    d.heightCm !== "" ||
    d.weightValue !== ""
  );
}
