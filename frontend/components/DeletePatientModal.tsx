"use client";

import { useState } from "react";

import { ModalPortal } from "@/components/ModalPortal";
import { deletePatient } from "@/lib/api";
import { useModalDismiss } from "@/lib/useModalDismiss";

type DeletePatientModalProps = {
  open: boolean;
  patientId: string;
  patientName: string;
  onClose: () => void;
  onDeleted: () => void;
};

export function DeletePatientModal({ open, patientId, patientName, onClose, onDeleted }: DeletePatientModalProps) {
  const [options, setOptions] = useState({
    delete_uploads: true,
    delete_chats: true,
    delete_record_files: true,
    delete_vector_data: true,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const overlayDismiss = useModalDismiss(onClose);

  if (!open) {
    return null;
  }

  return (
    <ModalPortal>
      <div className="modal-overlay" role="presentation" {...overlayDismiss}>
        <div className="modal-panel max-w-xl">
        <p className="eyebrow">Delete Patient</p>
        <h2 className="mt-2 text-2xl font-semibold text-text-primary">Remove {patientName} data</h2>
        <p className="mt-3 text-sm leading-6 text-text-secondary">Choose what should be removed. All options start enabled to match the backend defaults.</p>

        <div className="mt-5 space-y-3">
          {[
            ["delete_uploads", "Uploaded source files"],
            ["delete_chats", "Chat histories"],
            ["delete_record_files", "Patient record files"],
            ["delete_vector_data", "Vector memory collections"],
          ].map(([key, label]) => (
            <label className="toggle-row" key={key}>
              <span className="field-label">{label}</span>
              <input
                checked={options[key as keyof typeof options]}
                className="h-5 w-5 accent-[var(--color-error)]"
                onChange={(event) => setOptions({ ...options, [key]: event.target.checked })}
                type="checkbox"
              />
            </label>
          ))}
        </div>

        {error ? <div className="status-error mt-4">{error}</div> : null}

        <div className="mt-6 flex justify-end gap-3 border-t border-border/80 pt-5">
          <button className="button-secondary" onClick={onClose} type="button">
            Cancel
          </button>
          <button
            className="button-danger"
            disabled={submitting}
            onClick={async () => {
              try {
                setSubmitting(true);
                setError(null);
                await deletePatient(patientId, options);
                onDeleted();
              } catch (err) {
                setError(err instanceof Error ? err.message : "Could not delete patient");
              } finally {
                setSubmitting(false);
              }
            }}
            type="button"
          >
            {submitting ? "Deleting…" : "Delete patient"}
          </button>
        </div>
        </div>
      </div>
    </ModalPortal>
  );
}
