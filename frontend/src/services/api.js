import axios from "axios";

// In Docker: requests go through CRA proxy to backend
// In manual dev: set REACT_APP_API_URL=http://localhost:8000
const API_URL = process.env.REACT_APP_API_URL || "";

const api = axios.create({ baseURL: API_URL, timeout: 60000 });

// Attach JWT on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Global 401 handler
api.interceptors.response.use(
  (res) => res,
  (err) => {
    // If 401 on login endpoint, do NOT redirect or reload the page!
    // Let the LoginPage component handle the error and display toast/error banner.
    const isLoginRequest = err.config?.url?.includes("/auth/login");
    if (err.response?.status === 401 && !isLoginRequest) {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  }
);

// ── Auth ───────────────────────────────────────────────────────────────────
export const login    = (email, password) => api.post("/auth/login",   { email, password });
export const register = (data)            => api.post("/auth/register", data);
export const getMe    = ()                => api.get("/auth/me");

// ── Applications ───────────────────────────────────────────────────────────
export const getApplications   = ()     => api.get("/applications/");
export const getApplication    = (id)   => api.get(`/applications/${id}`);
export const createApplication = (data) => api.post("/applications/", data);
export const deleteApplication = (id)   => api.delete(`/applications/${id}`);

// ── Documents ──────────────────────────────────────────────────────────────
// In-flight processing tracking per document ID and per application doc type
const processingDocIds = new Set();
const processingTypes = new Set(); // format: `${appId}:${docType}`

export const markDocumentProcessing = (docId, appId = null, docType = null) => {
  if (docId) processingDocIds.add(Number(docId));
  if (appId && docType) processingTypes.add(`${appId}:${docType}`);
};

export const unmarkDocumentProcessing = (docId, appId = null, docType = null) => {
  if (docId) processingDocIds.delete(Number(docId));
  if (appId && docType) processingTypes.delete(`${appId}:${docType}`);
};

export const isDocumentProcessing = (docId) => {
  return docId ? processingDocIds.has(Number(docId)) : false;
};

export const isTypeProcessing = (appId, docType) => {
  return (appId && docType) ? processingTypes.has(`${appId}:${docType}`) : false;
};

export const uploadDocument  = (formData) =>
  api.post("/documents/upload", formData);

// Multi-pass OCR legitimately takes 1–3 minutes.
// Use a 4-minute per-request timeout so the global 60 s default is not hit.
const PROCESS_DOCUMENT_TIMEOUT_MS = 4 * 60 * 1000; // 240 000 ms

export const processDocument = async (docId) => {
  const numId = Number(docId);
  if (numId) processingDocIds.add(numId);
  try {
    return await api.post(
      `/documents/process/${docId}`,
      null,
      { timeout: PROCESS_DOCUMENT_TIMEOUT_MS }
    );
  } finally {
    if (numId) processingDocIds.delete(numId);
  }
};

/**
 * Returns true when an Axios error is a client-side request timeout
 * (i.e. the per-request or instance timeout fired before the server responded).
 * Use this to show "Document processing timed out" instead of the raw Axios message.
 */
export const isTimeoutError = (err) =>
  err?.code === 'ECONNABORTED' || err?.message?.toLowerCase().includes('timeout');

export const getDocuments          = (appId)   => api.get(`/documents/${appId}`);
export const validateRequiredDocumentsApi = (appId) => api.get(`/documents/validate-required/${appId}`);

export const deleteDocument = (docId) => {
  if (isDocumentProcessing(docId)) {
    return Promise.reject(new Error("Cannot delete document while it is being processed. Please wait."));
  }
  return api.delete(`/documents/${docId}`);
};

export const deleteDocumentsByType = (appId, docType) => {
  if (isTypeProcessing(appId, docType)) {
    return Promise.reject(new Error("Cannot delete document while it is being processed. Please wait."));
  }
  return api.delete(`/documents/by-type/${appId}/${docType}`);
};

// ── Government Verification Result Screenshots ─────────────────────────
// Dedicated endpoints stored in government_verification_screenshots.
// Decoupled from borrower KYC documents; does NOT trigger OCR.
export const uploadGovScreenshot = (appId, verificationType, file) => {
  const formData = new FormData();
  formData.append('verification_type', verificationType);
  formData.append('file', file);
  return api.post(`/gov-screenshots/${appId}/upload`, formData);
};
export const getGovScreenshots = (appId) => api.get(`/gov-screenshots/${appId}`);
export const deleteGovScreenshot = (screenshotId) => api.delete(`/gov-screenshots/${screenshotId}`);

// ── Verification ───────────────────────────────────────────────────────────
export const runVerification = (appId) => api.post(`/verify/${appId}`);

// Agentic (CrewAI multi-agent) verification — can take longer due to
// multiple sequential LLM calls across 5 agents, so use an extended timeout.
export const runAgenticVerification = (appId) =>
  api.post(`/verify/${appId}/agentic`, null, { timeout: 6 * 60 * 1000 });

export const getVerificationStatus = (appId) => api.get(`/verify/${appId}/status`);

// ── Reports ────────────────────────────────────────────────────────────────
export const getReport       = (appId) => api.get(`/report/${appId}`);
export const downloadReport  = (appId) => `${API_URL}/report/${appId}/download`;
export const downloadPDF     = (appId) => api.get(`/report/${appId}/download`, { responseType: 'blob' });
export const regeneratePdf   = (appId) => api.post(`/report/${appId}/regenerate-pdf`);
export const getReportReview = (appId) => api.get(`/report/${appId}/review`);
export const editParticularReview = (appId, data) => api.post(`/report/${appId}/review/edit`, data);
export const toggleEvidenceSelection = (appId, data) => api.post(`/report/${appId}/review/evidence`, data);
export const revalidateReportReview = (appId) => api.post(`/report/${appId}/review/revalidate`);
export const generateReportPdf = (appId) =>
  api.post(`/api/reports/${appId}/generate-pdf`, {}, {
    responseType: 'blob'
  });

// ── Dashboard ─────────────────────────────────────────────────────────────
export const getDashboardStats = (branch = "") => api.get(`/dashboard/stats${branch ? `?branch=${encodeURIComponent(branch)}` : ""}`);

export const submitSiteVerification = (appId, data) => api.post(`/applications/${appId}/site_verification`, data);

export const submitJointApplicant = (appId, data) => api.post(`/applications/${appId}/joint_applicants`, data);

export const submitPropertyDetails = (appId, data) => api.post(`/applications/${appId}/property_details`, data);

export const submitGovVerification = (appId, data) => api.post(`/applications/${appId}/gov_verification`, data);

export const updateApplicantDetails = (appId, data) => api.put(`/applications/${appId}/applicant_details`, data);

export default api;

