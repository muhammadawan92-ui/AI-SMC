"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, FileText, Image, X, CheckCircle, Loader2 } from "lucide-react";
import { cn, fmtBytes } from "@/lib/utils";
import { uploadsApi } from "@/lib/api";
import type { FileType } from "@/types";

interface UploadZoneProps {
  fileType: FileType;
  projectId?: string;
  onSuccess?: (fileId: string, fileName: string) => void;
  accept?: Record<string, string[]>;
  label?: string;
  description?: string;
  className?: string;
}

type UploadStatus = "idle" | "uploading" | "success" | "error";

export function UploadZone({
  fileType,
  projectId,
  onSuccess,
  accept,
  label,
  description,
  className,
}: UploadZoneProps) {
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [fileName, setFileName] = useState("");
  const [fileSize, setFileSize] = useState(0);
  const [error, setError] = useState("");

  const defaultAccept: Record<string, string[]> = {
    pine_script: { "text/plain": [".pine", ".txt"] } as Record<string, string[]>,
    mql5: { "text/plain": [".mq5", ".mq4", ".mqh", ".txt"] } as Record<string, string[]>,
    backtest_report: { "text/html": [".htm", ".html"], "text/csv": [".csv"] } as Record<string, string[]>,
    mt5_log: { "text/plain": [".log", ".txt"] } as Record<string, string[]>,
    screenshot: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] } as Record<string, string[]>,
    csv: { "text/csv": [".csv"] } as Record<string, string[]>,
    trade_history: { "text/csv": [".csv"], "text/html": [".htm", ".html"] } as Record<string, string[]>,
    notes: {
      "text/plain": [".txt", ".md"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    } as Record<string, string[]>,
  };

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (!acceptedFiles.length) return;
      const file = acceptedFiles[0];
      setFileName(file.name);
      setFileSize(file.size);
      setStatus("uploading");
      setError("");

      const formData = new FormData();
      formData.append("file", file);
      formData.append("file_type", fileType);
      if (projectId) formData.append("project_id", projectId);

      try {
        const { data } = await uploadsApi.upload(formData);
        setStatus("success");
        onSuccess?.(data.id, file.name);
      } catch (e: unknown) {
        setStatus("error");
        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Upload failed";
        setError(msg);
      }
    },
    [fileType, projectId, onSuccess]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: accept || defaultAccept[fileType],
    maxFiles: 1,
  });

  const reset = () => {
    setStatus("idle");
    setFileName("");
    setError("");
  };

  const isImage = fileType === "screenshot";

  return (
    <div className={cn("relative", className)}>
      {status === "success" ? (
        <div className="border-2 border-green-700/50 bg-green-900/10 rounded-xl p-6 flex items-center gap-4">
          <CheckCircle size={24} className="text-green-400 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="font-medium text-green-300 truncate">{fileName}</div>
            <div className="text-sm text-green-600">{fmtBytes(fileSize)} — Uploaded successfully</div>
          </div>
          <button onClick={reset} className="text-gray-500 hover:text-gray-300">
            <X size={16} />
          </button>
        </div>
      ) : (
        <div
          {...getRootProps()}
          className={cn(
            "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all",
            isDragActive
              ? "border-brand-500 bg-brand-900/20"
              : status === "error"
              ? "border-red-700/50 bg-red-900/10"
              : "border-gray-700 bg-gray-900/50 hover:border-gray-600 hover:bg-gray-800/50"
          )}
        >
          <input {...getInputProps()} />
          <div className="flex flex-col items-center gap-3">
            {status === "uploading" ? (
              <Loader2 size={32} className="text-brand-400 animate-spin" />
            ) : isImage ? (
              <Image size={32} className="text-gray-500" />
            ) : (
              <Upload size={32} className="text-gray-500" />
            )}
            <div>
              {status === "uploading" ? (
                <p className="text-sm text-gray-300">Uploading {fileName}…</p>
              ) : (
                <>
                  <p className="text-sm font-medium text-gray-300">
                    {label || `Drop ${fileType.replace("_", " ")} here`}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    {description || "or click to browse"}
                  </p>
                </>
              )}
            </div>
            {status === "error" && (
              <p className="text-xs text-red-400 max-w-xs">{error}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
