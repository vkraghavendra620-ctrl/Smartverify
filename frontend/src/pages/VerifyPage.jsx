import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CheckCircle, XCircle, AlertTriangle, Play, Loader,
  Bot, Users, Info, FileEdit
} from 'lucide-react';
import toast from 'react-hot-toast';
import { getApplications, getApplication, getDocuments, getReport, runAgenticVerification, getVerificationStatus } from '../services/api';
import ScoreGauge from '../components/ui/ScoreGauge';
import StatusBadge from '../components/ui/StatusBadge';

function sanitizeError(msg) {
  if (!msg) return 'Verification failed';
  let s = typeof msg === 'object' ? JSON.stringify(msg) : String(msg);
  s = s.replace(/gsk_[A-Za-z0-9_-]{10,}/g, '[REDACTED_API_KEY]');
  s = s.replace(/AIza[0-9A-Za-z_-]{25,}/g, '[REDACTED_API_KEY]');
  s = s.replace(/AQ\.[A-Za-z0-9_-]{20,}/g, '[REDACTED_API_KEY]');
  s = s.replace(/(Bearer\s+)[A-Za-z0-9._-]{15,}/gi, '$1[REDACTED_TOKEN]');
  s = s.replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, '[REDACTED_EMAIL]');
  s = s.replace(/\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b/g, '[REDACTED_PAN]');
  s = s.replace(/\b\d{4}\s\d{4}\s\d{4}\b/g, '[REDACTED_AADHAAR]');
  s = s.replace(/\b\d{12}\b/g, '[REDACTED_AADHAAR]');
  s = s.replace(/\b(?:\+91[\s-]?)?[6-9]\d{9}\b/g, '[REDACTED_PHONE]');
  return s;
}

export default function VerifyPage() {
  const navigate              = useNavigate();
  const [apps, setApps]       = useState([]);
  const [appId, setAppId]     = useState('');
  const [result, setResult]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError]     = useState(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [pollStatusNotice, setPollStatusNotice] = useState(null);
  const isSubmittingRef       = React.useRef(false);
  const pollIntervalRef       = React.useRef(null);
  const timerIntervalRef      = React.useRef(null);

  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    };
  }, []);

  // Readiness state
  const [readiness, setReadiness] = useState({
    applicationCreated: false,
    documentsUploaded: false,
    ocrCompleted: false,
    nlpCompleted: false,
    panAadhaarExtracted: false,
    aadhaarVerification: false,
    panVerification: false,
    screenshotUploaded: false,
    siteVerification: false,
  });

  // Load applications list
  useEffect(() => { getApplications().then((r) => setApps(r.data)).catch(() => {}); }, []);

  // Check readiness and load existing report when appId changes
  useEffect(() => {
    setError(null);
    setResult(null);

    if (!appId) {
      setReadiness({
        applicationCreated: false,
        documentsUploaded: false,
        ocrCompleted: false,
        nlpCompleted: false,
        panAadhaarExtracted: false,
        aadhaarVerification: false,
        panVerification: false,
        screenshotUploaded: false,
        siteVerification: false,
      });
      return;
    }

    setChecking(true);
    Promise.all([
      getApplication(appId),
      getDocuments(appId),
      getReport(appId).catch(() => null),
    ])
    .then(([appRes, docsRes, reportRes]) => {
      const app = appRes?.data;
      const docs = docsRes?.data || [];

      // If existing report is found, populate result
      if (reportRes?.data && (reportRes.data.status || reportRes.data.verification_score !== undefined)) {
        setResult(reportRes.data);
      }

      // Logic for documents (require at least aadhaar and pan for primary applicant)
      const hasAadhaar = docs.some(d => (d.document_type?.value || d.document_type) === 'aadhaar' && !d.joint_applicant_index);
      const hasPan = docs.some(d => (d.document_type?.value || d.document_type) === 'pan' && !d.joint_applicant_index);
      const documentsUploaded = hasAadhaar && hasPan;

      // OCR & NLP logic for mandatory docs
      const mandatoryDocs = docs.filter(d => ['aadhaar', 'pan'].includes(d.document_type?.value || d.document_type) && !d.joint_applicant_index);
      let ocrCompleted = false;
      let nlpCompleted = false;
      if (mandatoryDocs.length > 0) {
        ocrCompleted = mandatoryDocs.every(d => d.extracted_text && d.extracted_text.trim() !== '');
        nlpCompleted = mandatoryDocs.every(d => d.structured_data && d.structured_data.trim() !== '');
      }

      // Extracted PAN & Aadhaar numbers from structured data (primary applicant)
      let extractedPan = false;
      let extractedAadhaar = false;
      docs.forEach(d => {
        if (!d.joint_applicant_index && d.structured_data) {
          try {
            const sd = JSON.parse(d.structured_data);
            if (sd.pan_number) extractedPan = true;
            if (sd.aadhaar_number) extractedAadhaar = true;
          } catch (e) {
            // ignore JSON error
          }
        }
      });
      const panAadhaarExtracted = extractedPan && extractedAadhaar;

      // Gov Verification logic — V3: requires BOTH steps
      const govVer = app?.gov_verification;
      const aadhaarVerification = govVer ? (govVer.aadhaar_validity_status && govVer.aadhaar_validity_status !== 'Pending') : false;
      const panVerification = govVer ? (govVer.pan_aadhaar_link_status && govVer.pan_aadhaar_link_status !== 'Pending') : false;
      const screenshotUploaded = govVer ? (!!govVer.aadhaar_screenshot_path || !!govVer.screenshot_path) : false;

      // Site verification images logic (optional supporting evidence)
      const SITE_DOCUMENT_TYPES = new Set([
        'site_front_view',
        'site_side_view',
        'site_interior',
        'site_entrance',
      ]);
      const siteVerification = docs.some(d => SITE_DOCUMENT_TYPES.has(d.document_type)) || true;

      setReadiness({
        applicationCreated: !!app,
        documentsUploaded,
        ocrCompleted,
        nlpCompleted,
        panAadhaarExtracted,
        aadhaarVerification,
        panVerification,
        screenshotUploaded,
        siteVerification,
      });
    })
    .catch(err => {
      console.error("Error checking readiness", err);
    })
    .finally(() => {
      setChecking(false);
    });
  }, [appId]);

  const isReady = Object.values(readiness).every(Boolean);
  const completedCount = Object.values(readiness).filter(Boolean).length;
  const totalChecks = Object.keys(readiness).length;

  const handleVerify = async () => {
    // Immediate synchronous debounce against duplicate clicks in the same event tick
    if (isSubmittingRef.current || loading) {
      return;
    }
    if (!appId) return toast.error('Select an application first');
    if (!isReady) return toast.error('Please complete all verification readiness steps first.');

    isSubmittingRef.current = true;
    setLoading(true);
    setError(null);
    setResult(null);
    setElapsedSeconds(0);
    setPollStatusNotice(null);

    // Track elapsed time every second (budget up to 300s)
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    const startTime = Date.now();
    timerIntervalRef.current = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);

    let finished = false;

    const stopAll = () => {
      finished = true;
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current);
        timerIntervalRef.current = null;
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      setLoading(false);
      isSubmittingRef.current = false;
      setPollStatusNotice(null);
    };

    // Polling function: regularly check if background job finished
    const startPolling = () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = setInterval(async () => {
        if (finished) return;
        try {
          const statusRes = await getVerificationStatus(appId);
          const data = statusRes.data;
          if (data?.report) {
            setResult(data.report);
            toast.success('Multi-agent verification complete!');
            stopAll();
          } else if (data?.is_timed_out) {
            setError('Verification workflow timed out after 300.0s.');
            toast.error('Verification workflow timed out after 300.0s.');
            stopAll();
          } else if (data?.is_active) {
            setPollStatusNotice(`Agents actively processing on server... (${Math.floor((Date.now() - startTime) / 1000)}s / 300s budget)`);
          }
        } catch (e) {
          // ignore status poll errors
        }
      }, 4000);
    };

    startPolling();

    try {
      const { data } = await runAgenticVerification(appId);
      if (!finished) {
        setResult(data);
        toast.success('Multi-agent verification complete!');
        stopAll();
      }
    } catch (err) {
      if (finished) return;

      if (err.response?.status === 409) {
        toast.info('Verification is actively running in background. Polling for report...', { id: 'dup-verify' });
        setPollStatusNotice('Verification is in progress in the background. Waiting for agents to finish...');
        return;
      }

      // Check if background worker is actually still running
      try {
        const checkRes = await getVerificationStatus(appId);
        if (checkRes.data?.report) {
          setResult(checkRes.data.report);
          toast.success('Multi-agent verification complete!');
          stopAll();
          return;
        } else if (checkRes.data?.is_active) {
          setPollStatusNotice('Connection interrupted, but backend verification is still actively processing. Waiting for completion...');
          return;
        }
      } catch (checkErr) {
        // Fall through
      }

      const rawDetail = err.response?.data?.detail || err.message || 'Verification failed';
      const cleanError = sanitizeError(rawDetail);
      setError(cleanError);
      toast.error(cleanError);
      stopAll();
    }
  };

  const ChecklistItem = ({ passed, label, tooltip }) => (
    <div className={`flex items-center gap-3 p-2 rounded-lg transition-colors group relative cursor-help
        ${passed ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}
         title={tooltip}>
      {passed ? (
        <CheckCircle className="w-5 h-5 text-emerald-600 flex-shrink-0" />
      ) : (
        <XCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
      )}
      <span className="font-medium text-sm flex-1">
        {!passed && '❌ Missing: '} {label}
      </span>
      {!passed && (
        <Info className="w-4 h-4 text-red-400 opacity-50 group-hover:opacity-100 transition-opacity" />
      )}
    </div>
  );

  return (
    <div className='max-w-4xl mx-auto space-y-6'>
      {/* Controls */}
      <div className='card space-y-5'>
        <div>
          <label className='block text-sm font-medium text-slate-700 mb-1'>Select Application</label>
          <select value={appId} onChange={(e) => { setAppId(e.target.value); setResult(null); }} className='input max-w-md'>
            <option value=''>— Choose application —</option>
            {apps.map((a) => (
              <option key={a.id} value={a.id}>
                #{a.id} — {a.branch || 'Unknown Branch'} | {a.loan_type || '—'} | ₹{Number(a.loan_amount).toLocaleString('en-IN')}
              </option>
            ))}
          </select>
        </div>

        {/* Verification Pipeline Display */}
        <div>
          <label className='block text-sm font-medium text-slate-700 mb-2'>Verification Pipeline</label>
          <div className='p-4 rounded-xl border-2 border-purple-500 bg-purple-50/50'>
            <div className='flex items-start gap-3'>
              <Users className='w-5 h-5 mt-0.5 flex-shrink-0 text-purple-600' />
              <div>
                <p className='text-sm font-semibold text-slate-800 flex items-center gap-2'>
                  Multi-Agent Verification (CrewAI) <Bot className='w-3.5 h-3.5 text-purple-500' />
                </p>
                <p className='text-xs text-slate-600 mt-1 leading-relaxed'>
                  AI-powered multi-agent verification pipeline for document analysis, extraction, verification, fraud analysis and compliance reporting.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Verification Readiness Checklist */}
        {appId && (
          <div className="border border-slate-200 rounded-xl overflow-hidden mt-6">
            <div className="bg-slate-50 px-4 py-3 border-b border-slate-200 flex justify-between items-center">
              <h3 className="font-bold text-slate-700">Verification Readiness</h3>
              <span className={`text-sm font-semibold px-2.5 py-1 rounded-full ${isReady ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-200 text-slate-700'}`}>
                {checking ? <Loader className="w-4 h-4 animate-spin" /> : `${completedCount} / ${totalChecks} Complete`}
              </span>
            </div>
            
            <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
              <ChecklistItem passed={readiness.applicationCreated} label="Application Created" tooltip="An application profile must be initialized." />
              <ChecklistItem passed={readiness.documentsUploaded} label="Required Documents Uploaded" tooltip="Aadhaar and PAN documents are mandatory." />
              <ChecklistItem passed={readiness.ocrCompleted} label="OCR Completed" tooltip="Extracted text is required for mandatory documents." />
              <ChecklistItem passed={readiness.nlpCompleted} label="NLP Extraction Completed" tooltip="Structured data must be extracted via NLP." />
              <ChecklistItem passed={readiness.panAadhaarExtracted} label="PAN & Aadhaar Extracted" tooltip="Both PAN and Aadhaar numbers must be extracted by the NLP pipeline." />
              <ChecklistItem passed={readiness.aadhaarVerification} label="Aadhaar Validity Verified (UIDAI)" tooltip="Step 1: Aadhaar validity must be checked via UIDAI portal." />
              <ChecklistItem passed={readiness.panVerification} label="PAN–Aadhaar Link Verified" tooltip="Step 2: PAN-Aadhaar linkage must be verified on Income Tax portal." />
              <ChecklistItem passed={readiness.screenshotUploaded} label="Verification Screenshots Uploaded" tooltip="Screenshots from the portals must be attached." />
              <ChecklistItem passed={readiness.siteVerification} label="Site Verification Completed" tooltip="Site verification records or supporting photos." />
            </div>
            
            {!isReady && !checking && (
              <div className="px-4 py-3 bg-red-50 text-red-700 text-sm font-medium border-t border-red-100 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" />
                Complete all required verification steps before running AI verification.
              </div>
            )}
          </div>
        )}

        <button onClick={handleVerify} disabled={loading || !appId || (!isReady && !checking)}
          className={`btn-primary flex items-center gap-2 mt-4 ${(!isReady || checking) ? 'opacity-50 cursor-not-allowed' : ''}`}>
          {loading ? <Loader className='w-4 h-4 animate-spin' /> : <Play className='w-4 h-4' />}
          {loading ? `Agents working… (${elapsedSeconds}s / max 300s)` : 'Run Verification'}
        </button>

        {/* Live Multi-Agent Execution Progress Banner */}
        {loading && (
          <div className="p-4 rounded-xl border border-indigo-200 bg-indigo-50/80 space-y-2 mt-4 shadow-sm animate-pulse">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-sm text-indigo-900">
                <Loader className="w-4 h-4 animate-spin text-indigo-600 flex-shrink-0" />
                <span>Multi-Agent Verification Pipeline Active</span>
              </div>
              <span className="text-xs font-bold font-mono px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800">
                {elapsedSeconds}s / 300s max
              </span>
            </div>
            <p className="text-xs text-indigo-700 leading-relaxed">
              {pollStatusNotice || 'Executing 5-agent pipeline: Document Analysis → Financial Extraction → Policy Verification → Fraud Assessment → Compliance Synthesis.'}
            </p>
            <div className="w-full bg-indigo-200 h-1.5 rounded-full overflow-hidden">
              <div
                className="bg-indigo-600 h-1.5 transition-all duration-1000 ease-linear rounded-full"
                style={{ width: `${Math.min(100, Math.round((elapsedSeconds / 300) * 100))}%` }}
              />
            </div>
          </div>
        )}

        {/* Persistent Error Banner */}
        {error && (
          <div className="p-4 rounded-xl border border-red-200 bg-red-50 text-red-800 space-y-2 mt-4">
            <div className="flex items-center gap-2 font-semibold text-sm">
              <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0" />
              <span>Verification Pipeline Error</span>
            </div>
            <p className="text-xs text-red-700 leading-relaxed font-mono bg-red-100/70 p-2.5 rounded-lg overflow-x-auto whitespace-pre-wrap break-words">
              {error}
            </p>
            <p className="text-xs text-red-600">
              Please review the readiness checklist or provider configuration and try again.
            </p>
          </div>
        )}
      </div>

      {/* Results */}
      {result && (
        <div className='space-y-4'>
          {/* Distinguish empty report from report with findings */}
          {((!result.status || result.status === 'pending') && (!result.extracted_info || Object.keys(result.extracted_info).length === 0) && (!result.verification_score)) ? (
            <div className='card border-l-4 border-slate-300 bg-slate-50 p-4 text-xs text-slate-600 flex items-center gap-2'>
              <Info className='w-4 h-4 text-slate-400 flex-shrink-0' />
              <span>No verification findings recorded yet for this application. Click "Run Verification" above to begin.</span>
            </div>
          ) : (
            <>
              {/* Agent summary */}
              {result.agent_summary ? (
                <div className='card border-l-4 border-purple-400 bg-purple-50/40'>
                  <h3 className='font-semibold text-purple-800 mb-2 flex items-center gap-2'>
                    <Bot className='w-5 h-5' /> Multi-Agent Verification Summary
                  </h3>
                  <p className='text-sm text-slate-700 leading-relaxed'>{result.agent_summary}</p>
                </div>
              ) : (
                <div className='card border-l-4 border-amber-300 bg-amber-50/60 p-3.5 text-xs text-amber-800 flex items-center gap-2'>
                  <Info className='w-4 h-4 text-amber-600 flex-shrink-0' />
                  <span>Verification findings were successfully recorded, but no narrative AI summary was generated.</span>
                </div>
              )}

              {/* Scores + Status */}
              <div className='card flex flex-wrap items-center justify-between gap-6'>
                <div className='flex gap-8'>
                  <ScoreGauge label='Verification' score={result.verification_score !== undefined ? result.verification_score : 0} />
                  <ScoreGauge label='Risk Score'   score={result.risk_score !== undefined ? result.risk_score : 0} />
                </div>
                <div className='space-y-2'>
                  <div className='flex items-center gap-2'>
                    <span className='text-sm text-slate-500'>Status:</span>
                    <StatusBadge status={result.status || 'unknown'} />
                  </div>
                  <div className='flex items-center gap-2'>
                    <span className='text-sm text-slate-500'>Fraud Flag:</span>
                    {result.fraud_flag ? (
                      <span className='flex items-center gap-1 text-red-600 text-sm font-medium'>
                        <AlertTriangle className='w-4 h-4' /> Flagged
                      </span>
                    ) : (
                      <span className='flex items-center gap-1 text-emerald-600 text-sm font-medium'>
                        <CheckCircle className='w-4 h-4' /> Clear
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Action to Review Report (Phase 8 Authoritative Pipeline) */}
              <div className='card bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-100 flex flex-col sm:flex-row items-center justify-between gap-4 p-4'>
                <div>
                  <h4 className='font-semibold text-slate-800 text-sm'>Verification Findings Recorded</h4>
                  <p className='text-xs text-slate-600 mt-0.5'>
                    Multi-agent verification results and findings have been normalized and saved. Proceed to the 26-particular report review to inspect, edit, or publish the final PDF.
                  </p>
                </div>
                <button
                  type='button'
                  onClick={() => navigate(`/report/${appId}/review`)}
                  className='btn-primary flex items-center gap-2 whitespace-nowrap shadow-sm text-sm py-2 px-4'
                >
                  <FileEdit className='w-4 h-4' /> Review &amp; Edit Report
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
