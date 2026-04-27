"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PatientSettings } from "@/components/PatientSettings";
import { getPatient, type Patient } from "@/lib/api";

export default function PatientSettingsPage() {
  const params = useParams<{ id: string }>();
  const patientId = params.id;
  const router = useRouter();
  const [patient, setPatient] = useState<Patient | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const patientData = await getPatient(patientId);
        setPatient(patientData);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load patient settings");
      }
    }

    void load();
  }, [patientId]);

  if (error) {
    return <div className="status-error">{error}</div>;
  }

  if (!patient) {
    return <div className="empty-state">Loading patient settings…</div>;
  }

  return (
    <div className="mx-auto max-w-4xl animate-fade-up">
      <Link className="mb-4 inline-flex text-sm font-medium text-primary hover:text-primary-hover" href={`/patient/${patient.id}`}>
        ← Back to patient workspace
      </Link>
      <PatientSettings
        onDeleted={() => {
          router.push("/");
        }}
        onPatientSaved={(updatedPatient) => {
          setPatient(updatedPatient);
        }}
        patient={patient}
      />
    </div>
  );
}
