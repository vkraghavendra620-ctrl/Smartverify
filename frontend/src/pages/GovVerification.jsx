import React, { useState, useCallback, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ShieldCheck, ExternalLink, CheckCircle2, XCircle,
  Clock, Eye, User, FileImage, Trash2, ClipboardCheck,
  BadgeInfo, Save, AlertTriangle, Fingerprint,
  CheckCircle, AlertCircle, Loader2
} from 'lucide-react';
import toast from 'react-hot-toast';
import {
  submitGovVerification,
  getApplication,
  getDocuments,
  getApplications,
  uploadDocument,
  processDocument,
  deleteDocument,
  deleteDocumentsByType
} from '../services/api';

// ─── Constants ──────────────────────────────────────────────────────────────

const AADHAAR_STATUS_OPTIONS = [
  { value: 'Valid',                  label: 'Valid' },
  { value: 'Invalid',                label: 'Invalid' },
  { value: 'Pending',                label: 'Pending' },
  { value: 'Manual Review Required', label: 'Manual Review Required' },
];

const PAN_STATUS_OPTIONS = [
  { value: 'Linked',                 label: 'Linked' },
  { value: 'Not Linked',             label: 'Not Linked' },
  { value: 'Pending',                label: 'Pending' },
  { value: 'Manual Review Required', label: 'Manual Review Required' },
];

const LINK_UIDAI_PORTAL    = 'https://myaadhaar.uidai.gov.in/check-aadhaar-validity/en';
const LINK_AADHAAR_PORTAL  = 'https://eportal.incometax.gov.in/iec/foservices/#/pre-login/link-aadhaar-status';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function extractFromDocuments(docs) {
  let panNumber     = null;
  let aadhaarNumber = null;
  let applicantName = null;

  const sorted = [...docs].sort(
    (a, b) => new Date(b.created_at) - new Date(a.created_at)
  );

  const parseSD = (doc) => {
    if (!doc.structured_data) return {};
    try { return JSON.parse(doc.structured_data); }
    catch { return {}; }
  };

  // Priority pass: prefer matching doc type
  for (const doc of sorted) {
    const sd = parseSD(doc);
    if (!panNumber     && doc.document_type === 'pan'     && sd.pan_number)     panNumber     = sd.pan_number;
    if (!aadhaarNumber && doc.document_type === 'aadhaar' && sd.aadhaar_number) aadhaarNumber = sd.aadhaar_number;
    if (!applicantName && sd.applicant_name) applicantName = sd.applicant_name;
  }

  // Fallback pass
  for (const doc of sorted) {
    const sd = parseSD(doc);
    if (!panNumber     && sd.pan_number)     panNumber     = sd.pan_number;
    if (!aadhaarNumber && sd.aadhaar_number) aadhaarNumber = sd.aadhaar_number;
    if (!applicantName && sd.applicant_name) applicantName = sd.applicant_name;
  }

  return {
    panNumber:     panNumber     || null,
    aadhaarNumber: aadhaarNumber || null,
    applicantName: applicantName || null,
    panSource:     panNumber     ? 'ocr' : 'none',
    aadhaarSource: aadhaarNumber ? 'ocr' : 'none',
    nameSource:    applicantName ? 'ocr' : 'none',
  };
}

// ─── Status Config ────────────────────────────────────────────────────────────

function statusConfig(status) {
  switch (status) {
    case 'Valid':
    case 'Linked':
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700 border border-emerald-200',
        dot:    'bg-emerald-500',
        icon:   <CheckCircle2 className="w-3.5 h-3.5" />,
        label:  status,
        ring:   'ring-emerald-300',
        header: 'border-l-4 border-emerald-400',
      };
    case 'Invalid':
    case 'Not Linked':
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-700 border border-red-200',
        dot:    'bg-red-500',
        icon:   <XCircle className="w-3.5 h-3.5" />,
        label:  status,
        ring:   'ring-red-300',
        header: 'border-l-4 border-red-400',
      };
    case 'Manual Review Required':
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-700 border border-blue-200',
        dot:    'bg-blue-500',
        icon:   <Eye className="w-3.5 h-3.5" />,
        label:  'Manual Review',
        ring:   'ring-blue-300',
        header: 'border-l-4 border-blue-400',
      };
    default:
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-700 border border-amber-200',
        dot:    'bg-amber-500',
        icon:   <Clock className="w-3.5 h-3.5" />,
        label:  'Pending',
        ring:   'ring-amber-300',
        header: 'border-l-4 border-amber-400',
      };
  }
}

// ─── Extracted Field Display ──────────────────────────────────────────────────

function ExtractedField({
  label,
  value,
  onChange,
  source,
  loading,
  loadingText,
  missingText,
  placeholder,
  confidenceLevel,
  confidenceScore,
  warningText
}) {
  if (loading) {
    return (
      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
          {label}
        </label>
        <div className="flex items-center gap-2 h-10 bg-blue-50 rounded-xl border border-blue-200 px-3 text-blue-700">
          <Loader2 className="w-4 h-4 text-blue-600 animate-spin flex-shrink-0" />
          <span className="text-sm font-medium">{loadingText || "Scanning image with OCR…"}</span>
        </div>
      </div>
    );
  }

  const hasValue = !!value;

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide">
          {label}
        </label>
        {source === 'ocr' && hasValue && confidenceLevel && (
          <span
            className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${
              confidenceLevel === 'high'
                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                : 'bg-amber-50 text-amber-700 border border-amber-200'
            }`}
          >
            {confidenceLevel === 'high' ? (
              <>
                <CheckCircle className="w-3 h-3 text-emerald-600" />
                High confidence
              </>
            ) : (
              <>
                <AlertTriangle className="w-3 h-3 text-amber-600" />
                Low confidence
              </>
            )}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={hasValue ? value : ''}
          onChange={(e) => onChange && onChange(e.target.value)}
          placeholder={placeholder || `Upload image below to extract ${label}`}
          className={`input font-mono tracking-wider border-slate-200
            ${hasValue
              ? 'bg-white text-slate-800 uppercase font-semibold'
              : 'bg-slate-50 text-slate-400 placeholder:text-slate-400 placeholder:italic placeholder:font-sans placeholder:tracking-normal'
            }`}
        />
        <div
          className="flex-shrink-0 p-2 rounded-lg bg-white border border-slate-200"
          title={source === 'ocr' ? 'Auto-extracted by OCR from uploaded image' : 'Editable field'}
        >
          <BadgeInfo className="w-4 h-4 text-slate-400" />
        </div>
      </div>

      {warningText && (
        <div className="mt-1.5 flex items-start gap-1.5 bg-amber-50/80 border border-amber-200 text-amber-800 rounded-lg p-2 text-xs">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-600 flex-shrink-0 mt-0.5" />
          <span>{warningText}</span>
        </div>
      )}

      <div className="mt-1.5 flex items-center gap-1.5">
        {source === 'ocr' && hasValue ? (
          <>
            <CheckCircle className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
            <span className="text-xs text-emerald-600 font-medium">
              Extracted from uploaded image (OCR)
            </span>
          </>
        ) : hasValue ? (
          <>
            <CheckCircle className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
            <span className="text-xs text-blue-600 font-medium">Entered manually</span>
          </>
        ) : (
          <>
            <AlertCircle className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
            <span className="text-xs text-slate-400">
              {missingText || 'Waiting for image upload — drop image in box below'}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Screenshot Dropzone ──────────────────────────────────────────────────────

function ScreenshotDropzone({ screenshot, onFileAccepted, onClear, label = 'Upload Verification Screenshot' }) {
  const onDrop = useCallback((accepted) => {
    if (accepted.length > 0) onFileAccepted(accepted[0]);
  }, [onFileAccepted]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'image/*': ['.jpg', '.jpeg', '.png', '.webp'], 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    multiple: false,
  });

  if (screenshot) {
    return (
      <div className="relative group rounded-xl overflow-hidden border border-slate-200 bg-slate-50">
        {screenshot.name.toLowerCase().endsWith('.pdf') ? (
          <div className="w-full h-44 flex flex-col items-center justify-center text-slate-500">
            <ClipboardCheck className="w-10 h-10 text-primary-500 mb-2" />
            <span className="font-semibold text-sm">{screenshot.name}</span>
          </div>
        ) : (
          <img
            src={screenshot.url}
            alt="Verification screenshot"
            className="w-full h-44 object-cover"
          />
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-all flex items-center justify-center">
          <button
            onClick={(e) => { e.stopPropagation(); onClear(); }}
            className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1.5 bg-red-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg"
          >
            <Trash2 className="w-3.5 h-3.5" /> Remove
          </button>
        </div>
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent px-3 py-2 pointer-events-none">
          <p className="text-white text-xs truncate">{screenshot.name}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      {...getRootProps()}
      className={`border-2 border-dashed rounded-xl p-5 flex flex-col items-center justify-center gap-2 cursor-pointer transition-all min-h-[9rem]
        ${isDragActive
          ? 'border-primary-400 bg-primary-50'
          : 'border-slate-300 bg-slate-50 hover:border-primary-400 hover:bg-primary-50/50'
        }`}
    >
      <input {...getInputProps()} />
      <div className={`w-10 h-10 rounded-full flex items-center justify-center transition-colors
        ${isDragActive ? 'bg-primary-100' : 'bg-slate-100'}`}>
        <FileImage className={`w-5 h-5 ${isDragActive ? 'text-primary-600' : 'text-slate-400'}`} />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-slate-700">
          {isDragActive ? 'Drop screenshot here' : label}
        </p>
        <p className="text-xs text-slate-400 mt-0.5">Drag &amp; drop or click — JPG, PNG, PDF</p>
      </div>
    </div>
  );
}

// ─── Status Button Group ──────────────────────────────────────────────────────

function StatusButtonGroup({ options, currentValue, onChange }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {options.map((opt) => {
        const cfg      = statusConfig(opt.value);
        const isActive = currentValue === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs font-semibold border-2 transition-all duration-150 text-left
              ${isActive
                ? `${cfg.badge.replace('inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ', '')} border-current shadow-sm`
                : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50'
              }`}
          >
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isActive ? cfg.dot : 'bg-slate-300'}`} />
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ─── Initial State ────────────────────────────────────────────────────────────

const INITIAL_STATE = {
  aadhaarStatus:    'Pending',
  aadhaarScreenshot: null,
  panStatus:        'Pending',
  panScreenshot:    null,
  date:             '',
  time:             '',
  officerName:      '',
  remarks:          '',
};

// ─── Main Page ────────────────────────────────────────────────────────────────

// LocalStorage helpers for Gov Verification persistence per application
const getGovStorageKey = (appId) => `gov_verification_${appId}`;

const loadGovCache = (appId) => {
  if (!appId) return null;
  try {
    const raw = localStorage.getItem(getGovStorageKey(appId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
};

const saveGovCache = (appId, partial) => {
  if (!appId) return;
  try {
    const current = loadGovCache(appId) || {};
    const updated = { ...current, ...partial };
    localStorage.setItem(getGovStorageKey(appId), JSON.stringify(updated));
  } catch (e) {
    console.warn('Failed to save to localStorage', e);
  }
};

export default function GovVerificationPage() {
  const { appId: routeAppId } = useParams();
  const navigate   = useNavigate();
  const [data, setData]         = useState({ ...INITIAL_STATE });
  const [isSaving, setIsSaving] = useState(false);

  // Application selection state.
  // NOTE: intentionally NOT restored from localStorage on mount — every fresh
  // page load/app start must begin with no application selected (and therefore
  // no screenshots/OCR data shown) until the user explicitly picks or is
  // routed to a specific application. See routeAppId handling below.
  const [apps, setApps]                   = useState([]);
  const [selectedAppId, setSelectedAppId] = useState(routeAppId || '');

  // OCR-extracted values
  const [panNumber,           setPanNumber]           = useState('');
  const [aadhaarNumber,       setAadhaarNumber]       = useState('');
  const [applicantName,       setApplicantName]       = useState('');
  const [panSource,           setPanSource]           = useState('none');
  const [aadhaarSource,       setAadhaarSource]       = useState('none');
  const [nameSource,          setNameSource]          = useState('none');
  const [isExtractingAadhaar, setIsExtractingAadhaar] = useState(false);
  const [isExtractingPan,     setIsExtractingPan]     = useState(false);

  // Quality warnings & field-level confidence ratings
  const [aadhaarQualityWarning,  setAadhaarQualityWarning]  = useState(null);
  const [panQualityWarning,      setPanQualityWarning]      = useState(null);
  const [aadhaarConfidenceLevel, setAadhaarConfidenceLevel] = useState(null);
  const [panConfidenceLevel,     setPanConfidenceLevel]     = useState(null);
  const [nameConfidenceLevel,    setNameConfidenceLevel]    = useState(null);
  const [aadhaarConfidenceScore, setAadhaarConfidenceScore] = useState(null);
  const [panConfidenceScore,     setPanConfidenceScore]     = useState(null);
  const [aadhaarFieldWarning,    setAadhaarFieldWarning]    = useState(null);
  const [panFieldWarning,        setPanFieldWarning]        = useState(null);

  // Aadhaar-specific extracted fields & confidence
  const [aadhaarName,           setAadhaarName]           = useState('');
  const [aadhaarOcrText,        setAadhaarOcrText]        = useState('');
  const [aadhaarNameConfidence, setAadhaarNameConfidence] = useState(null);

  // PAN-specific extracted fields & confidence
  const [panName,               setPanName]               = useState('');
  const [panOcrText,            setPanOcrText]            = useState('');
  const [panNameConfidence,     setPanNameConfidence]     = useState(null);

  // 1. Fetch applications list on mount.
  // Only auto-select an application when the URL explicitly names one
  // (routeAppId) — e.g. navigating in from the Applications list. A bare
  // page load/refresh with no routeAppId must NOT auto-pick an application,
  // so the page starts empty (dropdown on "— Select an application —") with
  // no screenshots or OCR data until the user chooses one themselves.
  useEffect(() => {
    getApplications()
      .then((r) => {
        const list = r.data || [];
        setApps(list);
        if (routeAppId) {
          setSelectedAppId(String(routeAppId));
        }
      })
      .catch((err) => console.error('Failed to load applications', err));
  }, []);

  // Update selectedAppId if route changes
  useEffect(() => {
    if (routeAppId) {
      setSelectedAppId(String(routeAppId));
    }
  }, [routeAppId]);

  // 2. Load and sync verification audit & document data when selectedAppId changes
  useEffect(() => {
    if (!selectedAppId) return;

    // A. INSTANT RESTORE from localStorage (per-application cache, used only
    // when the user has explicitly selected/navigated to this application —
    // not on a fresh app start, since selectedAppId is never auto-restored)
    const cached = loadGovCache(selectedAppId);
    if (cached) {
      setData(prev => ({
        ...prev,
        aadhaarStatus:     cached.aadhaarRemoved ? 'Pending' : (cached.aadhaarStatus || prev.aadhaarStatus),
        panStatus:         cached.panRemoved     ? 'Pending' : (cached.panStatus     || prev.panStatus),
        officerName:       cached.officerName   !== undefined ? cached.officerName   : prev.officerName,
        date:              cached.date          !== undefined ? cached.date          : prev.date,
        time:              cached.time          !== undefined ? cached.time          : prev.time,
        remarks:           cached.remarks       !== undefined ? cached.remarks       : prev.remarks,
        aadhaarScreenshot: cached.aadhaarRemoved ? null : (cached.aadhaarScreenshot || null),
        panScreenshot:     cached.panRemoved     ? null : (cached.panScreenshot     || null),
      }));

      if (cached.aadhaarRemoved) {
        setAadhaarNumber('');
        setAadhaarSource('none');
        setAadhaarName('');
        setAadhaarOcrText('');
        setAadhaarNameConfidence(null);
        setAadhaarQualityWarning(null);
        setAadhaarConfidenceLevel(null);
        setAadhaarConfidenceScore(null);
        setAadhaarFieldWarning(null);
      } else {
        if (cached.aadhaarNumber !== undefined) setAadhaarNumber(cached.aadhaarNumber);
        if (cached.aadhaarSource !== undefined) setAadhaarSource(cached.aadhaarSource);
        if (cached.aadhaarName !== undefined) setAadhaarName(cached.aadhaarName);
        if (cached.aadhaarOcrText !== undefined) setAadhaarOcrText(cached.aadhaarOcrText);
        if (cached.aadhaarNameConfidence !== undefined) setAadhaarNameConfidence(cached.aadhaarNameConfidence);
        setAadhaarQualityWarning(cached.aadhaarQualityWarning || null);
        setAadhaarConfidenceLevel(cached.aadhaarConfidenceLevel || null);
        setAadhaarConfidenceScore(cached.aadhaarConfidenceScore || null);
        setAadhaarFieldWarning(cached.aadhaarFieldWarning || null);
      }

      if (cached.panRemoved) {
        setPanNumber('');
        setPanSource('none');
        setPanName('');
        setPanOcrText('');
        setPanNameConfidence(null);
        setPanQualityWarning(null);
        setPanConfidenceLevel(null);
        setPanConfidenceScore(null);
        setPanFieldWarning(null);
      } else {
        if (cached.panNumber !== undefined) setPanNumber(cached.panNumber);
        if (cached.panSource !== undefined) setPanSource(cached.panSource);
        if (cached.panName !== undefined) setPanName(cached.panName);
        if (cached.panOcrText !== undefined) setPanOcrText(cached.panOcrText);
        if (cached.panNameConfidence !== undefined) setPanNameConfidence(cached.panNameConfidence);
        setPanQualityWarning(cached.panQualityWarning || null);
        setPanConfidenceLevel(cached.panConfidenceLevel || null);
        setPanConfidenceScore(cached.panConfidenceScore || null);
        setPanFieldWarning(cached.panFieldWarning || null);
      }

      if (cached.applicantName !== undefined) setApplicantName(cached.applicantName);
      if (cached.nameSource !== undefined) setNameSource(cached.nameSource);
      if (cached.nameConfidenceLevel !== undefined) setNameConfidenceLevel(cached.nameConfidenceLevel);
    }

    // B. AUTHORITATIVE BACKEND SYNC (loads saved documents from SQLite database)
    Promise.all([
      getApplication(selectedAppId),
      getDocuments(selectedAppId)
    ])
      .then(([appRes, docsRes]) => {
        const appData = appRes.data || {};
        const gov = appData.gov_verification;
        const docs = docsRes.data || [];

        // 1. Audit info from gov_verification table
        let auditPatch = {};
        if (gov) {
          const parts = gov.timestamp ? gov.timestamp.split(' ') : [];
          auditPatch = {
            aadhaarStatus: gov.aadhaar_validity_status || 'Pending',
            panStatus:     gov.pan_aadhaar_link_status  || 'Pending',
            officerName:   gov.officer_name || '',
            date:          parts[0] || '',
            time:          parts.slice(1).join(' ') || '',
            remarks:       gov.remarks || '',
          };
        }

        // 2. Documents from documents table
        const sortedDocs = [...docs].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        const latestAadhaar = sortedDocs.find(d => d.document_type === 'aadhaar');
        const latestPan = sortedDocs.find(d => d.document_type === 'pan');

        const currentCache = loadGovCache(selectedAppId) || {};

        // Aadhaar document processing
        let aadhaarScreenshotObj = null;
        let newAadhaarNum = '';
        let newAadhaarSource = 'none';
        let newAadhaarName = '';
        let newAadhaarOcrText = '';
        let newAadhaarNameConf = null;
        let aadhaarQualWarn = null;
        let aadhaarConfLvl  = null;
        let aadhaarConfScr  = null;
        let aadhaarWarn     = null;

        if (latestAadhaar && !currentCache.aadhaarRemoved) {
          const fn = latestAadhaar.file_path.split(/[/\\]/).pop();
          aadhaarScreenshotObj = {
            id: latestAadhaar.id,
            url: `/uploads/${fn}`,
            name: latestAadhaar.original_name || fn,
          };
          newAadhaarOcrText = latestAadhaar.extracted_text || '';

          if (latestAadhaar.structured_data) {
            try {
              const sd = typeof latestAadhaar.structured_data === 'string'
                ? JSON.parse(latestAadhaar.structured_data)
                : latestAadhaar.structured_data;

              if (sd.image_quality?.warning) {
                aadhaarQualWarn = sd.image_quality.warning;
              }

              if (sd.fields?.aadhaar_number) {
                const f = sd.fields.aadhaar_number;
                if (f.value) {
                  newAadhaarNum = f.value;
                  newAadhaarSource = 'ocr';
                }
                aadhaarConfLvl = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
                aadhaarConfScr = f.confidence;
                aadhaarWarn = f.warning || null;
              } else if (sd.aadhaar_number) {
                newAadhaarNum = sd.aadhaar_number;
                newAadhaarSource = 'ocr';
                aadhaarConfLvl = 'high';
              }

              if (sd.fields?.applicant_name) {
                const f = sd.fields.applicant_name;
                if (f.value) {
                  newAadhaarName = f.value;
                  newAadhaarNameConf = f.confidence_level || 'high';
                }
              } else if (sd.applicant_name) {
                newAadhaarName = sd.applicant_name;
                newAadhaarNameConf = 'high';
              }
            } catch (e) {}
          }
        } else {
          auditPatch.aadhaarStatus = 'Pending';
        }

        // PAN document processing
        let panScreenshotObj = null;
        let newPanNum = '';
        let newPanSource = 'none';
        let newPanName = '';
        let newPanOcrText = '';
        let newPanNameConf = null;
        let panQualWarn = null;
        let panConfLvl  = null;
        let panConfScr  = null;
        let panWarn     = null;

        if (latestPan && !currentCache.panRemoved) {
          const fn = latestPan.file_path.split(/[/\\]/).pop();
          panScreenshotObj = {
            id: latestPan.id,
            url: `/uploads/${fn}`,
            name: latestPan.original_name || fn,
          };
          newPanOcrText = latestPan.extracted_text || '';

          if (latestPan.structured_data) {
            try {
              const sd = typeof latestPan.structured_data === 'string'
                ? JSON.parse(latestPan.structured_data)
                : latestPan.structured_data;

              if (sd.image_quality?.warning) {
                panQualWarn = sd.image_quality.warning;
              }

              if (sd.fields?.pan_number) {
                const f = sd.fields.pan_number;
                if (f.value) {
                  newPanNum = f.value;
                  newPanSource = 'ocr';
                }
                panConfLvl = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
                panConfScr = f.confidence;
                panWarn = f.warning || null;
              } else if (sd.pan_number) {
                newPanNum = sd.pan_number;
                newPanSource = 'ocr';
                panConfLvl = 'high';
              }

              if (sd.fields?.applicant_name) {
                const f = sd.fields.applicant_name;
                if (f.value) {
                  newPanName = f.value;
                  newPanNameConf = f.confidence_level || 'high';
                }
              } else if (sd.applicant_name) {
                newPanName = sd.applicant_name;
                newPanNameConf = 'high';
              }
            } catch (e) {}
          }
        } else {
          auditPatch.panStatus = 'Pending';
        }

        // Applicant name resolution
        let resolvedAppName = '';
        let resolvedNameSource = 'none';
        let resolvedNameConf = null;

        if (currentCache.nameSource === 'manual' && currentCache.applicantName) {
          resolvedAppName = currentCache.applicantName;
          resolvedNameSource = 'manual';
        } else if (newAadhaarName) {
          resolvedAppName = newAadhaarName;
          resolvedNameSource = 'aadhaar';
          resolvedNameConf = newAadhaarNameConf;
        } else if (newPanName) {
          resolvedAppName = newPanName;
          resolvedNameSource = 'pan';
          resolvedNameConf = newPanNameConf;
        }

        setData(prev => ({
          ...prev,
          ...auditPatch,
          aadhaarScreenshot: aadhaarScreenshotObj,
          panScreenshot:     panScreenshotObj,
        }));

        setAadhaarNumber(newAadhaarNum);
        setAadhaarSource(newAadhaarSource);
        setAadhaarName(newAadhaarName);
        setAadhaarOcrText(newAadhaarOcrText);
        setAadhaarNameConfidence(newAadhaarNameConf);
        setAadhaarQualityWarning(aadhaarQualWarn);
        setAadhaarConfidenceLevel(aadhaarConfLvl);
        setAadhaarConfidenceScore(aadhaarConfScr);
        setAadhaarFieldWarning(aadhaarWarn);

        setPanNumber(newPanNum);
        setPanSource(newPanSource);
        setPanName(newPanName);
        setPanOcrText(newPanOcrText);
        setPanNameConfidence(newPanNameConf);
        setPanQualityWarning(panQualWarn);
        setPanConfidenceLevel(panConfLvl);
        setPanConfidenceScore(panConfScr);
        setPanFieldWarning(panWarn);

        setApplicantName(resolvedAppName);
        setNameSource(resolvedNameSource);
        setNameConfidenceLevel(resolvedNameConf);

        // Keep cache synced
        saveGovCache(selectedAppId, {
          ...auditPatch,
          aadhaarScreenshot: aadhaarScreenshotObj,
          panScreenshot:     panScreenshotObj,
          aadhaarNumber:     newAadhaarNum,
          panNumber:         newPanNum,
          aadhaarName:       newAadhaarName,
          panName:           newPanName,
          aadhaarOcrText:    newAadhaarOcrText,
          panOcrText:        newPanOcrText,
          aadhaarNameConfidence: newAadhaarNameConf,
          panNameConfidence:     newPanNameConf,
          applicantName:     resolvedAppName,
          aadhaarSource:     newAadhaarSource,
          panSource:         newPanSource,
          nameSource:        resolvedNameSource,
          nameConfidenceLevel: resolvedNameConf,
          aadhaarQualityWarning: aadhaarQualWarn,
          panQualityWarning:     panQualWarn,
          aadhaarConfidenceLevel: aadhaarConfLvl,
          panConfidenceLevel:    panConfLvl,
          aadhaarConfidenceScore: aadhaarConfScr,
          panConfidenceScore:    panConfScr,
          aadhaarFieldWarning:   aadhaarWarn,
          panFieldWarning:       panWarn,
          aadhaarRemoved:        !latestAadhaar || Boolean(currentCache.aadhaarRemoved),
          panRemoved:            !latestPan || Boolean(currentCache.panRemoved),
        });
      })
      .catch(err => {
        console.error('Error syncing gov verification data', err);
      });
  }, [selectedAppId]);

  const patchData = (patch) => {
    setData(prev => {
      const next = { ...prev, ...patch };
      saveGovCache(selectedAppId, patch);
      return next;
    });
  };

  // ── Aadhaar handlers ───────────────────────────────────────────────────
  const handleAadhaarStatusChange = (newStatus) => {
    if (newStatus === 'Valid' && !data.aadhaarScreenshot) {
      toast.error('Upload the UIDAI screenshot before marking as Valid.', { icon: '📸' });
      return;
    }
    const now = new Date();
    const newDate = newStatus !== 'Pending' ? now.toISOString().split('T')[0] : data.date;
    const newTime = newStatus !== 'Pending' ? now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true }) : data.time;
    patchData({
      aadhaarStatus: newStatus,
      date: newDate,
      time: newTime,
    });
  };

  const handleAadhaarScreenshotAccepted = async (file) => {
    const blobUrl = URL.createObjectURL(file);
    patchData({ aadhaarScreenshot: { url: blobUrl, name: file.name, size: file.size } });
    toast.success('Image selected — starting OCR extraction...');

    // Run OCR directly on the newly uploaded image
    const targetAppId = selectedAppId || (apps.length > 0 ? String(apps[0].id) : '1');
    setIsExtractingAadhaar(true);
    const toastId = toast.loading(`Scanning ${file.name} with OCR model...`);

    try {
      const formData = new FormData();
      formData.append('application_id', targetAppId);
      formData.append('document_type', 'aadhaar');
      formData.append('file', file);
      const upRes = await uploadDocument(formData);
      const procRes = await processDocument(upRes.data.id);

      const doc = procRes.data;
      const fn = doc.file_path ? doc.file_path.split(/[/\\]/).pop() : file.name;
      const permUrl = `/uploads/${fn}`;

      URL.revokeObjectURL(blobUrl);

      const screenshotObj = {
        id: doc.id,
        url: permUrl,
        name: doc.original_name || file.name,
      };

      patchData({ aadhaarScreenshot: screenshotObj });

      let newAadhaar = '';
      let newAadhaarSrc = 'none';
      let extractedName = '';
      let extractedOcrText = doc.extracted_text || '';
      let nameConf = null;

      let aadhaarQualWarn = null;
      let aadhaarConfLvl = null;
      let aadhaarConfScr = null;
      let aadhaarWarn = null;

      if (doc.structured_data) {
        const sd = typeof doc.structured_data === 'string'
          ? JSON.parse(doc.structured_data)
          : doc.structured_data;

        if (sd.image_quality?.warning) {
          aadhaarQualWarn = sd.image_quality.warning;
          toast('Notice: ' + sd.image_quality.warning, { icon: '⚠️' });
        }

        if (sd.fields?.aadhaar_number) {
          const f = sd.fields.aadhaar_number;
          if (f.value) {
            newAadhaar = f.value;
            newAadhaarSrc = 'ocr';
          }
          aadhaarConfLvl = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
          aadhaarConfScr = f.confidence;
          aadhaarWarn = f.warning || null;
        } else if (sd.aadhaar_number) {
          newAadhaar = sd.aadhaar_number;
          newAadhaarSrc = 'ocr';
          aadhaarConfLvl = 'high';
        }

        if (sd.fields?.applicant_name) {
          const f = sd.fields.applicant_name;
          if (f.value) {
            extractedName = f.value;
            nameConf = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
          }
        } else if (sd.applicant_name) {
          extractedName = sd.applicant_name;
          nameConf = 'high';
        }
      }

      setAadhaarNumber(newAadhaar);
      setAadhaarSource(newAadhaarSrc);
      setAadhaarName(extractedName);
      setAadhaarOcrText(extractedOcrText);
      setAadhaarNameConfidence(nameConf);

      let nextAppName = applicantName;
      let nextNameSource = nameSource;
      let nextNameConf = nameConfidenceLevel;

      if (extractedName && nameSource !== 'manual') {
        nextAppName = extractedName;
        nextNameSource = 'aadhaar';
        nextNameConf = nameConf;
        setApplicantName(nextAppName);
        setNameSource(nextNameSource);
        setNameConfidenceLevel(nextNameConf);
      }

      setAadhaarQualityWarning(aadhaarQualWarn);
      setAadhaarConfidenceLevel(aadhaarConfLvl);
      setAadhaarConfidenceScore(aadhaarConfScr);
      setAadhaarFieldWarning(aadhaarWarn);

      if (newAadhaar && aadhaarConfLvl === 'high') {
        toast.success(`OCR detected Aadhaar: ${newAadhaar} (High confidence)`, { id: toastId });
      } else if (newAadhaar) {
        toast(`OCR detected Aadhaar: ${newAadhaar} (Low confidence - please verify)`, { icon: '⚠️', id: toastId });
      } else {
        toast.error(aadhaarWarn || "Could not detect Aadhaar number. Please upload a clearer image.", { id: toastId });
      }

      saveGovCache(targetAppId, {
        aadhaarScreenshot: screenshotObj,
        aadhaarNumber: newAadhaar,
        aadhaarSource: newAadhaarSrc,
        aadhaarName: extractedName,
        aadhaarOcrText: extractedOcrText,
        aadhaarNameConfidence: nameConf,
        applicantName: nextAppName,
        nameSource: nextNameSource,
        nameConfidenceLevel: nextNameConf,
        aadhaarQualityWarning: aadhaarQualWarn,
        aadhaarConfidenceLevel: aadhaarConfLvl,
        aadhaarConfidenceScore: aadhaarConfScr,
        aadhaarFieldWarning: aadhaarWarn,
        aadhaarRemoved: false,
      });
    } catch (err) {
      console.error('OCR on uploaded image failed:', err);
      toast.error('OCR could not read document. You can enter details manually.', { id: toastId });
    } finally {
      setIsExtractingAadhaar(false);
    }
  };

  const handleClearAadhaarScreenshot = async () => {
    const docId = data.aadhaarScreenshot?.id;
    if (data.aadhaarScreenshot?.url?.startsWith('blob:')) {
      URL.revokeObjectURL(data.aadhaarScreenshot.url);
    }

    // 1. Immediately reset Aadhaar verification state & result
    patchData({
      aadhaarScreenshot: null,
      aadhaarStatus: 'Pending',
    });

    // 2. Immediately clear all Aadhaar-specific extracted fields
    setAadhaarNumber('');
    setAadhaarSource('none');
    setAadhaarName('');
    setAadhaarOcrText('');
    setAadhaarNameConfidence(null);
    setAadhaarQualityWarning(null);
    setAadhaarConfidenceLevel(null);
    setAadhaarConfidenceScore(null);
    setAadhaarFieldWarning(null);

    // 3. Resolve applicant name: if it came from Aadhaar, fallback to PAN name or clear
    let nextName = applicantName;
    let nextNameSource = nameSource;
    let nextNameConf = nameConfidenceLevel;
    if (nameSource === 'aadhaar' || (!panName && nameSource !== 'manual')) {
      if (panName) {
        nextName = panName;
        nextNameSource = 'pan';
        nextNameConf = panNameConfidence || 'high';
      } else {
        nextName = '';
        nextNameSource = 'none';
        nextNameConf = null;
      }
      setApplicantName(nextName);
      setNameSource(nextNameSource);
      setNameConfidenceLevel(nextNameConf);
    }

    // 4. Immediately persist cleared state to localStorage
    saveGovCache(selectedAppId, {
      aadhaarScreenshot: null,
      aadhaarStatus: 'Pending',
      aadhaarNumber: '',
      aadhaarSource: 'none',
      aadhaarName: '',
      aadhaarOcrText: '',
      aadhaarNameConfidence: null,
      aadhaarQualityWarning: null,
      aadhaarConfidenceLevel: null,
      aadhaarConfidenceScore: null,
      aadhaarFieldWarning: null,
      aadhaarRemoved: true,
      applicantName: nextName,
      nameSource: nextNameSource,
      nameConfidenceLevel: nextNameConf,
    });

    // 5. Backend deletion: delete from database, wipe files, reset gov_verification audit status
    try {
      if (docId) {
        await deleteDocument(docId);
      }
      await deleteDocumentsByType(selectedAppId, 'aadhaar');
      toast.success('Aadhaar document and extracted OCR data removed');
    } catch (e) {
      console.error('Failed to delete Aadhaar document on backend', e);
      toast.error('Failed to delete Aadhaar document on server');
    }
  };

  // ── PAN handlers ───────────────────────────────────────────────────────
  const handlePanStatusChange = (newStatus) => {
    if (newStatus === 'Linked' && !data.panScreenshot) {
      toast.error('Upload the portal screenshot before marking as Linked.', { icon: '📸' });
      return;
    }
    const now = new Date();
    const newDate = newStatus !== 'Pending' ? now.toISOString().split('T')[0] : data.date;
    const newTime = newStatus !== 'Pending' ? now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true }) : data.time;
    patchData({
      panStatus: newStatus,
      date: newDate,
      time: newTime,
    });
  };

  const handlePanScreenshotAccepted = async (file) => {
    const blobUrl = URL.createObjectURL(file);
    patchData({ panScreenshot: { url: blobUrl, name: file.name, size: file.size } });
    toast.success('Image selected — starting OCR extraction...');

    const targetAppId = selectedAppId || (apps.length > 0 ? String(apps[0].id) : '1');
    setIsExtractingPan(true);
    const toastId = toast.loading(`Scanning ${file.name} with OCR model...`);

    try {
      const formData = new FormData();
      formData.append('application_id', targetAppId);
      formData.append('document_type', 'pan');
      formData.append('file', file);
      const upRes = await uploadDocument(formData);
      const procRes = await processDocument(upRes.data.id);

      const doc = procRes.data;
      const fn = doc.file_path ? doc.file_path.split(/[/\\]/).pop() : file.name;
      const permUrl = `/uploads/${fn}`;

      URL.revokeObjectURL(blobUrl);

      const screenshotObj = {
        id: doc.id,
        url: permUrl,
        name: doc.original_name || file.name,
      };

      patchData({ panScreenshot: screenshotObj });

      let newPan = '';
      let newPanSrc = 'none';
      let extractedName = '';
      let extractedOcrText = doc.extracted_text || '';
      let nameConf = null;

      let panQualWarn = null;
      let panConfLvl = null;
      let panConfScr = null;
      let panWarn = null;

      if (doc.structured_data) {
        const sd = typeof doc.structured_data === 'string'
          ? JSON.parse(doc.structured_data)
          : doc.structured_data;

        if (sd.image_quality?.warning) {
          panQualWarn = sd.image_quality.warning;
          toast('Notice: ' + sd.image_quality.warning, { icon: '⚠️' });
        }

        if (sd.fields?.pan_number) {
          const f = sd.fields.pan_number;
          if (f.value) {
            newPan = f.value;
            newPanSrc = 'ocr';
          }
          panConfLvl = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
          panConfScr = f.confidence;
          panWarn = f.warning || null;
        } else if (sd.pan_number) {
          newPan = sd.pan_number;
          newPanSrc = 'ocr';
          panConfLvl = 'high';
        }

        if (sd.fields?.applicant_name) {
          const f = sd.fields.applicant_name;
          if (f.value) {
            extractedName = f.value;
            nameConf = f.confidence_level || (f.confidence >= 0.75 ? 'high' : 'low');
          }
        } else if (sd.applicant_name) {
          extractedName = sd.applicant_name;
          nameConf = 'high';
        }
      }

      setPanNumber(newPan);
      setPanSource(newPanSrc);
      setPanName(extractedName);
      setPanOcrText(extractedOcrText);
      setPanNameConfidence(nameConf);

      let nextAppName = applicantName;
      let nextNameSource = nameSource;
      let nextNameConf = nameConfidenceLevel;

      if (extractedName && (!applicantName || nameSource === 'none' || nameSource === 'pan')) {
        nextAppName = extractedName;
        nextNameSource = 'pan';
        nextNameConf = nameConf;
        setApplicantName(nextAppName);
        setNameSource(nextNameSource);
        setNameConfidenceLevel(nextNameConf);
      }

      setPanQualityWarning(panQualWarn);
      setPanConfidenceLevel(panConfLvl);
      setPanConfidenceScore(panConfScr);
      setPanFieldWarning(panWarn);

      if (newPan && panConfLvl === 'high') {
        toast.success(`OCR detected PAN: ${newPan} (High confidence)`, { id: toastId });
      } else if (newPan) {
        toast(`OCR detected PAN: ${newPan} (Low confidence - please verify)`, { icon: '⚠️', id: toastId });
      } else {
        toast.error(panWarn || "Could not detect PAN number. Please upload a clearer image.", { id: toastId });
      }

      saveGovCache(targetAppId, {
        panScreenshot: screenshotObj,
        panNumber: newPan,
        panSource: newPanSrc,
        panName: extractedName,
        panOcrText: extractedOcrText,
        panNameConfidence: nameConf,
        applicantName: nextAppName,
        nameSource: nextNameSource,
        nameConfidenceLevel: nextNameConf,
        panQualityWarning: panQualWarn,
        panConfidenceLevel: panConfLvl,
        panConfidenceScore: panConfScr,
        panFieldWarning: panWarn,
        panRemoved: false,
      });
    } catch (err) {
      console.error('OCR on uploaded PAN image failed:', err);
      toast.error("Could not detect PAN number. Please upload a clearer PAN card image.", { id: toastId });
    } finally {
      setIsExtractingPan(false);
    }
  };

  const handleClearPanScreenshot = async () => {
    const docId = data.panScreenshot?.id;
    if (data.panScreenshot?.url?.startsWith('blob:')) {
      URL.revokeObjectURL(data.panScreenshot.url);
    }

    // 1. Immediately reset PAN verification state & result
    patchData({
      panScreenshot: null,
      panStatus: 'Pending',
    });

    // 2. Immediately clear all PAN-specific extracted fields
    setPanNumber('');
    setPanSource('none');
    setPanName('');
    setPanOcrText('');
    setPanNameConfidence(null);
    setPanQualityWarning(null);
    setPanConfidenceLevel(null);
    setPanConfidenceScore(null);
    setPanFieldWarning(null);

    // 3. Resolve applicant name: if it came from PAN, fallback to Aadhaar name or clear
    let nextName = applicantName;
    let nextNameSource = nameSource;
    let nextNameConf = nameConfidenceLevel;
    if (nameSource === 'pan' || (!aadhaarName && nameSource !== 'manual')) {
      if (aadhaarName) {
        nextName = aadhaarName;
        nextNameSource = 'aadhaar';
        nextNameConf = aadhaarNameConfidence || 'high';
      } else {
        nextName = '';
        nextNameSource = 'none';
        nextNameConf = null;
      }
      setApplicantName(nextName);
      setNameSource(nextNameSource);
      setNameConfidenceLevel(nextNameConf);
    }

    // 4. Immediately persist cleared state to localStorage
    saveGovCache(selectedAppId, {
      panScreenshot: null,
      panStatus: 'Pending',
      panNumber: '',
      panSource: 'none',
      panName: '',
      panOcrText: '',
      panNameConfidence: null,
      panQualityWarning: null,
      panConfidenceLevel: null,
      panConfidenceScore: null,
      panFieldWarning: null,
      panRemoved: true,
      applicantName: nextName,
      nameSource: nextNameSource,
      nameConfidenceLevel: nextNameConf,
    });

    // 5. Backend deletion: delete from database, wipe files, reset gov_verification audit status
    try {
      if (docId) {
        await deleteDocument(docId);
      }
      await deleteDocumentsByType(selectedAppId, 'pan');
      toast.success('PAN document and extracted OCR data removed');
    } catch (e) {
      console.error('Failed to delete PAN document on backend', e);
      toast.error('Failed to delete PAN document on server');
    }
  };

  // ── Save ───────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!selectedAppId) return toast.error('Please select an application first');
    if (data.aadhaarStatus === 'Valid' && !data.aadhaarScreenshot) {
      toast.error('Screenshot is mandatory when Aadhaar status is Valid');
      return;
    }
    if (data.panStatus === 'Linked' && !data.panScreenshot) {
      toast.error('Screenshot is mandatory when PAN-Aadhaar status is Linked');
      return;
    }
    setIsSaving(true);
    try {
      const payload = {
        aadhaar_validity_status: data.aadhaarStatus,
        aadhaar_screenshot_path: data.aadhaarScreenshot ? data.aadhaarScreenshot.name : '',
        pan_aadhaar_link_status: data.panStatus,
        tax_receipt_status:      'pending',
        officer_name:            data.officerName,
        timestamp:               data.date && data.time ? `${data.date} ${data.time}` : '',
        remarks:                 data.remarks,
        screenshot_path:         data.panScreenshot ? data.panScreenshot.name : '',
      };
      await submitGovVerification(selectedAppId, payload);
      saveGovCache(selectedAppId, {
        ...payload,
        aadhaarStatus: data.aadhaarStatus,
        panStatus: data.panStatus,
        officerName: data.officerName,
        date: data.date,
        time: data.time,
        remarks: data.remarks,
      });
      toast.success('Verifications saved successfully!');
      navigate('/verify');
    } catch {
      toast.error('Failed to save verification');
    } finally {
      setIsSaving(false);
    }
  };

  const aadhaarDone = data.aadhaarStatus !== 'Pending';

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-20">

      {/* Page Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-800 flex items-center gap-2.5">
            <ShieldCheck className="w-6 h-6 text-primary-600" />
            GOVERNMENT IDENTITY VERIFICATION
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Two-step verification: UIDAI Aadhaar validity → Income Tax PAN-Aadhaar link.
          </p>
        </div>
        <button
          id="gov-save-btn"
          onClick={handleSave}
          disabled={isSaving || !selectedAppId}
          className="flex items-center gap-2 bg-primary-600 hover:bg-primary-700 text-white px-5 py-2.5 rounded-xl font-semibold transition-colors disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {isSaving ? 'Saving…' : 'Save Verifications'}
        </button>
      </div>

      {/* Application Selector */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex-1">
          <label className="block text-xs font-bold text-slate-600 uppercase tracking-wide mb-1">
            Active Loan Application
          </label>
          <select
            value={selectedAppId}
            onChange={(e) => setSelectedAppId(e.target.value)}
            className="input max-w-lg font-medium text-slate-800"
          >
            <option value="">— Select an application —</option>
            {apps.map((a) => (
              <option key={a.id} value={a.id}>
                #{a.id} — {a.applicant_name || 'Applicant'} ({a.branch || 'Main Branch'}) | ₹{Number(a.loan_amount).toLocaleString('en-IN')}
              </option>
            ))}
          </select>
        </div>
        {selectedAppId && (
          <span className="text-xs font-semibold px-3 py-1.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200 self-start sm:self-center">
            Application #{selectedAppId}
          </span>
        )}
      </div>

      {/* Step connector */}
      <div className="flex items-center gap-3 text-xs font-semibold text-slate-500 px-1">
        <span className={`flex items-center gap-1.5 px-3 py-1 rounded-full border ${aadhaarDone ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
          {aadhaarDone ? <CheckCircle2 className="w-3 h-3" /> : <Clock className="w-3 h-3" />}
          Step 1: Aadhaar
        </span>
        <div className="flex-1 border-t border-dashed border-slate-300" />
        <span className={`flex items-center gap-1.5 px-3 py-1 rounded-full border ${!aadhaarDone ? 'bg-slate-50 text-slate-400 border-slate-200' : data.panStatus !== 'Pending' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
          {!aadhaarDone ? <Clock className="w-3 h-3" /> : data.panStatus !== 'Pending' ? <CheckCircle2 className="w-3 h-3" /> : <Clock className="w-3 h-3" />}
          Step 2: PAN–Aadhaar
        </span>
      </div>

      {/* ═══════════════════════════════════════════════════════
          STEP 1 — AADHAAR VALIDITY (UIDAI)
      ═══════════════════════════════════════════════════════ */}
      <div className={`bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden transition-all duration-300 ${
        data.aadhaarStatus !== 'Pending' ? `ring-2 ${statusConfig(data.aadhaarStatus).ring}` : ''
      }`}>

        {/* Card header */}
        <div className={`px-6 py-4 ${statusConfig(data.aadhaarStatus).header} bg-gradient-to-r from-slate-50 to-white flex items-start justify-between`}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-blue-100 flex-shrink-0">
              <span className="text-blue-600 font-extrabold text-lg">1</span>
            </div>
            <div>
              <h3 className="font-bold text-slate-800 text-base">Government Aadhaar Verification</h3>
              <p className="text-xs text-slate-500 mt-0.5">Official UIDAI Aadhaar Validity Check</p>
            </div>
          </div>
          <span className={statusConfig(data.aadhaarStatus).badge}>
            {statusConfig(data.aadhaarStatus).icon}
            {statusConfig(data.aadhaarStatus).label}
          </span>
        </div>

        {/* Card body */}
        <div className="px-6 py-6 space-y-5">

          {/* Extracted data (editable) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 bg-slate-50 border border-slate-100 p-4 rounded-xl">
            <ExtractedField
              label="Applicant Name"
              value={applicantName}
              onChange={(val) => {
                setApplicantName(val);
                setNameSource(val ? 'manual' : 'none');
                saveGovCache(selectedAppId, { applicantName: val, nameSource: val ? 'manual' : 'none' });
              }}
              source={nameSource}
              loading={isExtractingAadhaar}
              loadingText="Scanning image with OCR model…"
              missingText="Upload Aadhaar card image below to extract"
              placeholder="Upload Aadhaar card image below to extract Name"
              confidenceLevel={nameConfidenceLevel}
            />
            <ExtractedField
              label="Applicant Aadhaar Number"
              value={aadhaarNumber}
              onChange={(val) => {
                setAadhaarNumber(val);
                setAadhaarSource(val ? 'manual' : 'none');
                saveGovCache(selectedAppId, { aadhaarNumber: val, aadhaarSource: val ? 'manual' : 'none' });
              }}
              source={aadhaarSource}
              loading={isExtractingAadhaar}
              loadingText="Scanning image with OCR model…"
              missingText="Upload Aadhaar card image below to extract"
              placeholder="Upload Aadhaar card image below to extract Number"
              confidenceLevel={aadhaarConfidenceLevel}
              confidenceScore={aadhaarConfidenceScore}
              warningText={aadhaarFieldWarning}
            />
          </div>

          {/* Image Quality Warning Alert */}
          {aadhaarQualityWarning && (
            <div className="flex items-start gap-2.5 bg-amber-50 border border-amber-200 text-amber-900 rounded-xl p-3.5 text-xs">
              <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <span className="font-bold">Image quality alert: </span>
                {aadhaarQualityWarning}
              </div>
            </div>
          )}

          {/* Portal Button */}
          <a
            id="uidai-portal-btn"
            href={LINK_UIDAI_PORTAL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full py-3.5 px-4 rounded-xl font-bold text-[15px] transition-all duration-150 bg-blue-600 hover:bg-blue-700 text-white shadow-md hover:shadow-lg"
          >
            <ExternalLink className="w-5 h-5" />
            Open UIDAI Portal — Check Aadhaar Validity
          </a>

          {/* Screenshot Upload */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              UIDAI Screenshot
              {data.aadhaarStatus === 'Valid' && <span className="text-red-500 ml-1">* Required</span>}
            </label>
            <ScreenshotDropzone
              screenshot={data.aadhaarScreenshot}
              onFileAccepted={handleAadhaarScreenshotAccepted}
              onClear={handleClearAadhaarScreenshot}
              label="Upload UIDAI Verification Screenshot"
            />
            {data.aadhaarStatus === 'Valid' && !data.aadhaarScreenshot && (
              <p className="text-xs text-red-500 mt-2 flex items-center gap-1">
                <XCircle className="w-3 h-3" /> Screenshot is mandatory for Valid status.
              </p>
            )}
          </div>

          {/* Verification Result */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              Aadhaar Verification Result
            </label>
            <StatusButtonGroup
              options={AADHAAR_STATUS_OPTIONS}
              currentValue={data.aadhaarStatus}
              onChange={handleAadhaarStatusChange}
            />
          </div>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════
          STEP 2 — PAN–AADHAAR LINK (Income Tax Portal)
      ═══════════════════════════════════════════════════════ */}
      <div className={`bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden transition-all duration-300 relative
        ${data.panStatus !== 'Pending' ? `ring-2 ${statusConfig(data.panStatus).ring}` : ''}
      `}>

        {/* Disabled overlay when Step 1 not done */}
        {!aadhaarDone && (
          <div className="absolute inset-0 bg-white/80 backdrop-blur-[2px] flex items-center justify-center z-10 rounded-2xl">
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-6 py-4 max-w-xs text-center shadow-sm">
              <AlertTriangle className="w-7 h-7 text-amber-500 mx-auto mb-2" />
              <p className="text-sm font-semibold text-amber-800">Complete Aadhaar Verification before continuing.</p>
              <p className="text-xs text-amber-600 mt-1">Step 1 must be finished first.</p>
            </div>
          </div>
        )}

        {/* Card header */}
        <div className={`px-6 py-4 ${statusConfig(data.panStatus).header} bg-gradient-to-r from-slate-50 to-white flex items-start justify-between`}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-indigo-100 flex-shrink-0">
              <span className="text-indigo-600 font-extrabold text-lg">2</span>
            </div>
            <div>
              <h3 className="font-bold text-slate-800 text-base">PAN–Aadhaar Link Verification</h3>
              <p className="text-xs text-slate-500 mt-0.5">Official Income Tax Portal Link Status</p>
            </div>
          </div>
          <span className={statusConfig(data.panStatus).badge}>
            {statusConfig(data.panStatus).icon}
            {statusConfig(data.panStatus).label}
          </span>
        </div>

        {/* Card body */}
        <div className="px-6 py-6 space-y-5">

          {/* Extracted data (editable) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 bg-slate-50 border border-slate-100 p-4 rounded-xl">
            <ExtractedField
              label="Applicant PAN Number"
              value={panNumber}
              onChange={(val) => {
                setPanNumber(val);
                setPanSource(val ? 'manual' : 'none');
                saveGovCache(selectedAppId, { panNumber: val, panSource: val ? 'manual' : 'none' });
              }}
              source={panSource}
              loading={isExtractingPan}
              loadingText="Scanning PAN image with OCR model…"
              missingText="Upload PAN card image below to extract"
              placeholder="Upload PAN card below to extract Number"
              confidenceLevel={panConfidenceLevel}
              confidenceScore={panConfidenceScore}
              warningText={panFieldWarning}
            />
            <ExtractedField
              label="Applicant Aadhaar Number"
              value={aadhaarNumber}
              onChange={(val) => {
                setAadhaarNumber(val);
                setAadhaarSource(val ? 'manual' : 'none');
                saveGovCache(selectedAppId, { aadhaarNumber: val, aadhaarSource: val ? 'manual' : 'none' });
              }}
              source={aadhaarSource}
              loading={isExtractingAadhaar}
              loadingText="Scanning image with OCR model…"
              missingText="Upload Aadhaar card image above to extract"
              placeholder="Upload Aadhaar card above to extract Number"
              confidenceLevel={aadhaarConfidenceLevel}
              confidenceScore={aadhaarConfidenceScore}
              warningText={aadhaarFieldWarning}
            />
          </div>

          {/* Image Quality Warning Alert */}
          {panQualityWarning && (
            <div className="flex items-start gap-2.5 bg-amber-50 border border-amber-200 text-amber-900 rounded-xl p-3.5 text-xs">
              <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <span className="font-bold">Image quality alert: </span>
                {panQualityWarning}
              </div>
            </div>
          )}

          {/* Portal Button */}
          <a
            id="incometax-portal-btn"
            href={LINK_AADHAAR_PORTAL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full py-3.5 px-4 rounded-xl font-bold text-[15px] transition-all duration-150 bg-indigo-600 hover:bg-indigo-700 text-white shadow-md hover:shadow-lg"
          >
            <ExternalLink className="w-5 h-5" />
            Open Income Tax Portal — Check PAN–Aadhaar Link
          </a>

          {/* Screenshot Upload */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              Portal Screenshot
              {data.panStatus === 'Linked' && <span className="text-red-500 ml-1">* Required</span>}
            </label>
            <ScreenshotDropzone
              screenshot={data.panScreenshot}
              onFileAccepted={handlePanScreenshotAccepted}
              onClear={handleClearPanScreenshot}
              label="Upload PAN–Aadhaar Link Screenshot"
            />
            {data.panStatus === 'Linked' && !data.panScreenshot && (
              <p className="text-xs text-red-500 mt-2 flex items-center gap-1">
                <XCircle className="w-3 h-3" /> Screenshot is mandatory for Linked status.
              </p>
            )}
          </div>

          {/* Verification Result */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              Link Verification Result
            </label>
            <StatusButtonGroup
              options={PAN_STATUS_OPTIONS}
              currentValue={data.panStatus}
              onChange={handlePanStatusChange}
            />
          </div>
        </div>
      </div>

      {/* Officer Details */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 px-6 py-6">
        <h4 className="text-sm font-bold text-slate-700 mb-4 flex items-center gap-2">
          <User className="w-4 h-4 text-slate-400" />
          Officer Details
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Officer Name</label>
            <input
              id="gov-officer-name"
              type="text"
              placeholder="e.g. John Doe"
              value={data.officerName}
              onChange={(e) => patchData({ officerName: e.target.value })}
              className="input"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Verification Date</label>
            <input
              type="date"
              value={data.date}
              onChange={(e) => patchData({ date: e.target.value })}
              className="input text-slate-700"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Verification Time</label>
            <input
              type="time"
              value={data.time}
              onChange={(e) => patchData({ time: e.target.value })}
              className="input text-slate-700"
            />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Remarks</label>
            <textarea
              rows={2}
              placeholder="Add any additional observations or notes…"
              value={data.remarks}
              onChange={(e) => patchData({ remarks: e.target.value })}
              className="input resize-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
