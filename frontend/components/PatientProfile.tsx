"use client";

import { useState } from "react";

import { ConditionsStep } from "@/components/intake/ConditionsStep";
import { DemographicsStep } from "@/components/intake/DemographicsStep";
import { LifestyleStep } from "@/components/intake/LifestyleStep";
import {
  buildProfile,
  demographicsFromProfile,
  socialHistoryFromProfile,
  type DemographicsDraft,
  type SocialHistoryDraft,
} from "@/components/intake/types";
import {
  updatePatientProfile,
  type Patient,
  type PatientCondition,
  type SexAssignedAtBirth,
  type SocialHistory,
} from "@/lib/api";
import { formatHeight, formatWeight } from "@/lib/units";

const SEX_LABELS: Record<SexAssignedAtBirth, string> = {
  male: "Male",
  female: "Female",
  intersex: "Intersex",
  undisclosed: "Prefer not to say",
};

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

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

function socialRows(social: SocialHistory): { label: string; value: string | null }[] {
  const tobacco = social.tobacco_status
    ? [titleCase(social.tobacco_status), social.tobacco_details].filter(Boolean).join(" — ")
    : null;
  const alcohol = social.alcohol_status
    ? [
        titleCase(social.alcohol_status),
        typeof social.alcohol_drinks_per_week === "number"
          ? `${social.alcohol_drinks_per_week} drinks/wk`
          : null,
      ]
        .filter(Boolean)
        .join(" — ")
    : null;
  const drugs = social.drug_status
    ? [titleCase(social.drug_status), social.drug_substances].filter(Boolean).join(" — ")
    : null;
  const sexual =
    social.sexual_activity_status === "active"
      ? [
          "Active",
          social.sexual_partners ? `partners: ${social.sexual_partners}` : null,
          social.sexual_partner_genders ? `gender(s): ${social.sexual_partner_genders}` : null,
          social.sexual_protection ? `protection: ${social.sexual_protection}` : null,
        ]
          .filter(Boolean)
          .join(" — ")
      : social.sexual_activity_status === "not_active"
        ? "Not active"
        : null;

  return [
    { label: "Tobacco", value: tobacco },
    { label: "Alcohol", value: alcohol },
    { label: "Recreational drugs", value: drugs },
    { label: "Sexual activity", value: sexual },
  ];
}

function ProfileView({ patient, onEdit }: { patient: Patient; onEdit: () => void }) {
  const profile = patient.profile ?? {};

  const dobValue = profile.dob
    ? (() => {
        const age = ageFromDob(profile.dob);
        return age === null ? profile.dob : `${profile.dob} (${age} yrs)`;
      })()
    : null;
  const sexValue = profile.sex_assigned_at_birth ? SEX_LABELS[profile.sex_assigned_at_birth] : null;
  const heightValue =
    typeof profile.height_cm === "number"
      ? `${formatHeight(profile.height_cm, "ftin")} · ${formatHeight(profile.height_cm, "cm")}`
      : null;
  const weightValue =
    typeof profile.weight_kg === "number"
      ? `${formatWeight(profile.weight_kg, "lbs")} · ${formatWeight(profile.weight_kg, "kg")}`
      : null;

  const social = profile.social_history ?? null;
  const socialEntries = social ? socialRows(social).filter((row) => row.value) : [];

  const conditions = profile.conditions ?? [];
  const byCategory = conditions.reduce<Record<string, PatientCondition[]>>((acc, condition) => {
    (acc[condition.category] ??= []).push(condition);
    return acc;
  }, {});
  const categories = Object.keys(byCategory);

  return (
    <div className="panel-scroll flex-1 space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Profile</p>
          <h2 className="text-2xl font-semibold text-text-primary">Patient details</h2>
          <p className="mt-2 text-sm leading-6 text-text-secondary">Demographics and conditions captured at intake.</p>
        </div>
        <button className="button-secondary" onClick={onEdit} type="button">
          Edit
        </button>
      </div>

      <div className="profile-grid">
        <Field label="Date of birth" value={dobValue} />
        <Field label="Sex assigned at birth" value={sexValue} />
        <Field label="Gender identity" value={profile.gender_identity ?? null} />
        <Field label="Height" value={heightValue} />
        <Field label="Weight" value={weightValue} />
      </div>

      <div>
        <h3 className="field-label mb-2">Lifestyle &amp; social history</h3>
        {socialEntries.length === 0 ? (
          <div className="empty-state">Not provided.</div>
        ) : (
          <div className="profile-grid">
            {socialEntries.map((row) => (
              <Field key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
        )}
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

function ProfileEditor({
  patient,
  onCancel,
  onSaved,
}: {
  patient: Patient;
  onCancel: () => void;
  onSaved: (patient: Patient) => void;
}) {
  const [demographics, setDemographics] = useState<DemographicsDraft>(() =>
    demographicsFromProfile(patient.profile),
  );
  const [social, setSocial] = useState<SocialHistoryDraft>(() =>
    socialHistoryFromProfile(patient.profile?.social_history),
  );
  const [conditions, setConditions] = useState<PatientCondition[]>(() => patient.profile?.conditions ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    try {
      setSaving(true);
      setError(null);
      const updated = await updatePatientProfile(patient.id, buildProfile(demographics, social, conditions));
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save profile");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel-scroll flex-1 space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Profile</p>
          <h2 className="text-2xl font-semibold text-text-primary">Edit patient details</h2>
          <p className="mt-2 text-sm leading-6 text-text-secondary">Update demographics, lifestyle, and conditions.</p>
        </div>
      </div>

      <DemographicsStep onChange={setDemographics} patientName={patient.name} value={demographics} />
      <LifestyleStep onChange={setSocial} patientName={patient.name} value={social} />
      <ConditionsStep onChange={setConditions} patientName={patient.name} selected={conditions} />

      {error ? <div className="status-error">{error}</div> : null}

      <div className="sticky bottom-0 flex justify-end gap-3 border-t border-border/80 bg-surface/95 py-3 backdrop-blur">
        <button className="button-secondary" disabled={saving} onClick={onCancel} type="button">
          Cancel
        </button>
        <button className="button-primary" disabled={saving} onClick={save} type="button">
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

export function PatientProfile({
  patient,
  onSaved,
}: {
  patient: Patient;
  onSaved?: (patient: Patient) => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <ProfileEditor
        onCancel={() => setEditing(false)}
        onSaved={(updated) => {
          onSaved?.(updated);
          setEditing(false);
        }}
        patient={patient}
      />
    );
  }

  return <ProfileView onEdit={() => setEditing(true)} patient={patient} />;
}
