"use client";

import type { Patient, PatientCondition, SexAssignedAtBirth } from "@/lib/api";
import { formatHeight, formatWeight } from "@/lib/units";

const SEX_LABELS: Record<SexAssignedAtBirth, string> = {
  male: "Male",
  female: "Female",
  intersex: "Intersex",
  undisclosed: "Prefer not to say",
};

function ageFromDob(dob: string): number | null {
  const birth = new Date(dob);
  if (Number.isNaN(birth.getTime())) {
    return null;
  }
  const now = new Date();
  let age = now.getFullYear() - birth.getFullYear();
  const monthDiff = now.getMonth() - birth.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < birth.getDate())) {
    age -= 1;
  }
  return age >= 0 ? age : null;
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="profile-field">
      <span className="field-label">{label}</span>
      <span className={value ? "text-text-primary" : "text-text-secondary"}>{value ?? "Not provided"}</span>
    </div>
  );
}

export function PatientProfile({ patient }: { patient: Patient }) {
  const dobValue = patient.dob
    ? (() => {
        const age = ageFromDob(patient.dob);
        return age === null ? patient.dob : `${patient.dob} (${age} yrs)`;
      })()
    : null;
  const sexValue = patient.sex_assigned_at_birth ? SEX_LABELS[patient.sex_assigned_at_birth] : null;
  const heightValue =
    typeof patient.height_cm === "number"
      ? `${formatHeight(patient.height_cm, "ftin")} · ${formatHeight(patient.height_cm, "cm")}`
      : null;
  const weightValue =
    typeof patient.weight_kg === "number"
      ? `${formatWeight(patient.weight_kg, "lbs")} · ${formatWeight(patient.weight_kg, "kg")}`
      : null;

  const conditions = patient.conditions ?? [];
  const byCategory = conditions.reduce<Record<string, PatientCondition[]>>((acc, condition) => {
    (acc[condition.category] ??= []).push(condition);
    return acc;
  }, {});
  const categories = Object.keys(byCategory);

  return (
    <div className="panel-scroll flex-1 space-y-6">
      <div>
        <p className="eyebrow">Profile</p>
        <h2 className="text-2xl font-semibold text-text-primary">Patient details</h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">Demographics and conditions captured at intake.</p>
      </div>

      <div className="profile-grid">
        <Field label="Date of birth" value={dobValue} />
        <Field label="Sex assigned at birth" value={sexValue} />
        <Field label="Gender identity" value={patient.gender_identity ?? null} />
        <Field label="Height" value={heightValue} />
        <Field label="Weight" value={weightValue} />
      </div>

      <div>
        <h3 className="field-label mb-2">Health conditions</h3>
        {categories.length === 0 ? (
          <div className="empty-state">No conditions recorded.</div>
        ) : (
          <div className="space-y-4">
            {categories.map((category) => (
              <div key={category}>
                <p className="text-sm font-semibold text-text-primary">{category}</p>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {byCategory[category].map((condition) => (
                    <span className="status-chip" key={`${condition.category}:${condition.code}`}>
                      {condition.label}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
