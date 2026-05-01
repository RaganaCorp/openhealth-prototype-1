"use client";

import { useRef, useState } from "react";

import { uploadFiles } from "@/lib/api";

type UploadAreaProps = {
  patientId: string;
  onUploaded: (jobId: string) => void;
};

export function UploadArea({ patientId, onUploaded }: UploadAreaProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(selectedFiles: File[]) {
    try {
      setUploading(true);
      setError(null);
      const result = await uploadFiles(patientId, selectedFiles);
      onUploaded(result.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload files");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <button
        className="upload-dropzone"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const droppedFiles = Array.from(event.dataTransfer.files);
          setFiles(droppedFiles);
        }}
        type="button"
      >
        <span className="block text-base font-semibold text-text-primary">Drag files here or choose from your computer</span>
        <span className="mt-2 block text-sm text-text-secondary">PDF, TIFF, TXT, HTML, and JSON are supported.</span>
      </button>

      <input
        className="hidden"
        multiple
        onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        ref={inputRef}
        type="file"
      />

      {files.length > 0 ? (
        <div className="mt-4 rounded-[20px] border border-border/80 bg-surface-elevated p-4">
          <p className="field-label">Selected files</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {files.map((file) => (
              <span className="status-chip" key={`${file.name}-${file.size}`}>
                {file.name}
              </span>
            ))}
          </div>
          <div className="mt-4 flex justify-end">
            <button className="button-primary" disabled={uploading} onClick={() => void submit(files)} type="button">
              {uploading ? "Uploading…" : "Upload and ingest"}
            </button>
          </div>
        </div>
      ) : null}

      {error ? <div className="status-error mt-4">{error}</div> : null}
    </div>
  );
}
