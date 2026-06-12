import type {
  PatientCondition,
  PatientIntake,
  PatientProfileData,
  SexAssignedAtBirth,
  SocialHistory,
} from "@/lib/api";
import { cmToFeetInches, feetInchesToCm, kgToLbs, round1 } from "@/lib/units";

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

// Lifestyle / social-history draft. Mirrors the canonical SocialHistory shape but
// keeps numeric fields as strings for controlled inputs; "" means "unset".
export type SocialHistoryDraft = {
  tobaccoStatus: NonNullable<SocialHistory["tobacco_status"]> | "";
  tobaccoDetails: string;
  alcoholStatus: NonNullable<SocialHistory["alcohol_status"]> | "";
  alcoholDrinksPerWeek: string;
  drugStatus: NonNullable<SocialHistory["drug_status"]> | "";
  drugSubstances: string;
  sexualActivityStatus: NonNullable<SocialHistory["sexual_activity_status"]> | "";
  sexualPartners: string;
  sexualPartnerGenders: string;
  sexualProtection: NonNullable<SocialHistory["sexual_protection"]> | "";
};

export const emptySocialHistory: SocialHistoryDraft = {
  tobaccoStatus: "",
  tobaccoDetails: "",
  alcoholStatus: "",
  alcoholDrinksPerWeek: "",
  drugStatus: "",
  drugSubstances: "",
  sexualActivityStatus: "",
  sexualPartners: "",
  sexualPartnerGenders: "",
  sexualProtection: "",
};

export function socialHistoryTouched(s: SocialHistoryDraft): boolean {
  return Object.values(s).some((v) => v !== "");
}

/** Build the canonical SocialHistory object, or null if nothing was entered. */
export function draftToSocialHistory(s: SocialHistoryDraft): SocialHistory | null {
  if (!socialHistoryTouched(s)) {
    return null;
  }
  return {
    tobacco_status: s.tobaccoStatus || null,
    tobacco_details: s.tobaccoDetails.trim() || null,
    alcohol_status: s.alcoholStatus || null,
    alcohol_drinks_per_week: s.alcoholDrinksPerWeek.trim() ? Number(s.alcoholDrinksPerWeek) : null,
    drug_status: s.drugStatus || null,
    drug_substances: s.drugSubstances.trim() || null,
    sexual_activity_status: s.sexualActivityStatus || null,
    sexual_partners: s.sexualPartners.trim() || null,
    sexual_partner_genders: s.sexualPartnerGenders.trim() || null,
    sexual_protection: s.sexualProtection || null,
  };
}

export function buildProfile(
  demographics: DemographicsDraft,
  social: SocialHistoryDraft,
  conditions: PatientCondition[],
): PatientProfileData {
  return {
    dob: demographics.dob || null,
    sex_assigned_at_birth: demographics.sex || null,
    gender_identity: demographics.genderIdentity.trim() || null,
    height_cm: draftHeightToCm(demographics),
    weight_kg: draftWeightToKg(demographics),
    social_history: draftToSocialHistory(social),
    conditions,
  };
}

export function buildIntakePayload(
  name: string,
  demographics: DemographicsDraft,
  social: SocialHistoryDraft,
  conditions: PatientCondition[],
): PatientIntake {
  return {
    name: name.trim(),
    profile: buildProfile(demographics, social, conditions),
  };
}

// Reverse mappers: seed editable drafts from a stored profile. Height/weight
// default to ft-in / lbs display (matching the intake defaults), populated from
// the canonical cm/kg.
export function demographicsFromProfile(profile: PatientProfileData | null | undefined): DemographicsDraft {
  const draft: DemographicsDraft = { ...emptyDemographics };
  if (!profile) {
    return draft;
  }
  draft.dob = profile.dob ?? "";
  draft.sex = profile.sex_assigned_at_birth ?? "";
  draft.genderIdentity = profile.gender_identity ?? "";
  if (typeof profile.height_cm === "number") {
    const { feet, inches } = cmToFeetInches(profile.height_cm);
    draft.heightFeet = String(feet);
    draft.heightInches = String(inches);
    draft.heightCm = String(round1(profile.height_cm));
  }
  if (typeof profile.weight_kg === "number") {
    draft.weightValue = String(Math.round(kgToLbs(profile.weight_kg)));
  }
  return draft;
}

export function socialHistoryFromProfile(social: SocialHistory | null | undefined): SocialHistoryDraft {
  if (!social) {
    return { ...emptySocialHistory };
  }
  return {
    tobaccoStatus: social.tobacco_status ?? "",
    tobaccoDetails: social.tobacco_details ?? "",
    alcoholStatus: social.alcohol_status ?? "",
    alcoholDrinksPerWeek: social.alcohol_drinks_per_week != null ? String(social.alcohol_drinks_per_week) : "",
    drugStatus: social.drug_status ?? "",
    drugSubstances: social.drug_substances ?? "",
    sexualActivityStatus: social.sexual_activity_status ?? "",
    sexualPartners: social.sexual_partners ?? "",
    sexualPartnerGenders: social.sexual_partner_genders ?? "",
    sexualProtection: social.sexual_protection ?? "",
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
