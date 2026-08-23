export type MechanicalViewMode =
  | "product"
  | "manufacturing_internals"
  | "clearance"
  | "exploded"
  | "presentation";

export interface MechanicalAnalysisJob {
  schema: "design-studio.mechanical-analysis-job/1";
  job_id: string;
  candidate_id: string;
  analysis_kind: string;
  geometry: {
    artifact_sha256: string;
    format: "step" | "iges" | "brep" | "fcstd" | "glb";
    units: "mm";
    artifact_uri?: string;
  };
  approval: {
    status: "approved";
    approved_by: string;
    approval_digest: string;
  };
  solver: { worker: string; backend: "auto" | "cpu" | "cuda" | "remote" };
  [key: string]: unknown;
}

export interface MechanicalAnalysisResult {
  schema: "design-studio.mechanical-analysis-result/1";
  job_id: string;
  candidate_id: string;
  analysis_kind: string;
  status: "pass" | "fail" | "incomplete" | "unavailable";
  worker: { name: string; version: string; backend: string };
  metrics: Record<string, number>;
  evidence: Record<string, unknown>;
  warnings: string[];
  release_eligible: boolean;
  result_digest: string;
}

export interface MechanicalFeedback {
  schema: "design-studio.mechanical-feedback/1";
  study_id: string;
  decision_status: "advisory_ranked" | "needs_exact_analysis_or_approval";
  recommendation: string | null;
  rankings: Array<Record<string, unknown>>;
  proposals: Array<Record<string, unknown>>;
  approval_policy: {
    ai_role: "advisory_rank_and_explain_only";
    ai_can_approve_geometry: false;
    human_approval_required: true;
    exact_worker_required: true;
    release_eligible: false;
  };
  feedback_digest: string;
}

