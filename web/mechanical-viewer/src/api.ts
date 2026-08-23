import type {
  MechanicalAnalysisJob,
  MechanicalAnalysisResult,
  MechanicalFeedback,
} from "./contracts";

async function postJson<T>(baseUrl: string, path: string, body: unknown): Promise<T> {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`DesignStudio API returned HTTP ${response.status}`);
  return (await response.json()) as T;
}

export function submitApprovedAnalysis(
  baseUrl: string,
  job: MechanicalAnalysisJob,
): Promise<MechanicalAnalysisResult> {
  return postJson<MechanicalAnalysisResult>(baseUrl, "/v1/mechanical-analysis", { job });
}

export function requestMechanicalFeedback(
  baseUrl: string,
  study: unknown,
): Promise<MechanicalFeedback> {
  return postJson<MechanicalFeedback>(baseUrl, "/v1/mechanical-feedback", { study });
}

