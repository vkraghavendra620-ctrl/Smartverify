import React, { useState, useCallback, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ShieldCheck, ExternalLink, CheckCircle2, XCircle,
  Clock, AlertTriangle, Eye, User, FileImage, Trash2,
  ClipboardCheck, BadgeInfo, RotateCcw, Info, Save
} from 'lucide-react';
import toast from 'react-hot-toast';
import { submitGovVerification, getApplication } from '../services/api';

// ─── Constants ──────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { value: 'pending',             label: 'Pending' },
  { value: 'verified',            label: 'Verified' },
  { value: 'verification_failed', label: 'Verification Failed' },
  { value: 'manual_review',       label: 'Manual Review' },
];

const AADHAAR_PORTAL = 'https://myaadhaar.uidai.gov.in/check-aadhaar-validity/en';
const PAN_PORTAL     = 'https://eportal.incometax.gov.in/iec/foservices/#/pre-login/verifyYourPAN';

// ─── Status helpers ──────────────────────────────────────────────────────────

function statusConfig(status) {
  switch (status) {
    case 'verified':
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700 border border-emerald-200',
        dot:    'bg-emerald-500',
        icon:   <CheckCircle2 className="w-3.5 h-3.5" />,
        label:  'Verified',
        ring:   'ring-emerald-300',
        header: 'border-l-4 border-emerald-400',
      };
    case 'verification_failed':
      return {
        badge:  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-700 border border-red-200',
        dot:    'bg-red-500',
        icon:   <XCircle className="w-3.5 h-3.5" />,
        label:  'Verification Failed',
        ring:   'ring-red-300',
        header: 'border-l-4 border-red-400',
      };
    case 'manual_review':
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

function overallStatus(aadhaar, pan) {
  const statuses = [aadhaar.status, pan.status];
  const allVerified  = statuses.every(s => s === 'verified');
  const anyFailed    = statuses.some(s => s === 'verification_failed');
  const anyManual    = statuses.some(s => s === 'manual_review');
  const someVerified = statuses.some(s => s === 'verified');

  if (allVerified)  return { label: 'Fully Verified',          ...statusConfig('verified') };
  if (anyFailed)    return { label: 'Verification Failed',     ...statusConfig('verification_failed') };
  if (anyManual)    return { label: 'Manual Review Required',  ...statusConfig('manual_review') };
  if (someVerified) return { label: 'Partially Verified',      ...statusConfig('pending') };
  return               { label: 'Pending',                     ...statusConfig('pending') };
}

// ─── Screenshot Dropzone ─────────────────────────────────────────────────────

function ScreenshotDropzone({ screenshot, onFileAccepted, onClear }) {
  const onDrop = useCallback((accepted) => {
    if (accepted.length > 0) onFileAccepted(accepted[0]);
  }, [onFileAccepted]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'image/*': ['.jpg', '.jpeg', '.png', '.webp'] },
    maxFiles: 1,
    multiple: false,
  });

  if (screenshot) {
    return (
      <div className="relative group rounded-xl overflow-hidden border border-slate-200 bg-slate-50">
        <img
          src={screenshot.url}
          alt="Verification screenshot"
          className="w-full h-44 object-cover"
        />
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-all flex items-center justify-center">
          <button
            onClick={onClear}
            className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1.5 bg-red-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg"
          >
            <Trash2 className="w-3.5 h-3.5" /> Remove
          </button>
        </div>
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent px-3 py-2">
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
          {isDragActive ? 'Drop screenshot here' : 'Upload Verification Screenshot'}
        </p>
        <p className="text-xs text-slate-400 mt-0.5">Drag & drop or click — JPG, PNG, WebP</p>
      </div>
    </div>
  );
}

// ─── Verification Card ───────────────────────────────────────────────────────

function VerificationCard({ title, icon, docNumber, numberLabel, portalUrl, portalLabel, accentColor, data, onChange }) {
  const cfg = statusConfig(data.status);

  const handleStatusChange = (newStatus) => {
    if (newStatus === 'verified' && !data.screenshot) {
      toast.error('Upload a verification screenshot before marking as Verified.', { icon: '📸' });
      return;
    }
    const timestamp = new Date().toLocaleString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true,
    });
    onChange({ status: newStatus, timestamp: newStatus !== 'pending' ? timestamp : '' });
  };

  const handleScreenshotAccepted = (file) => {
    const url = URL.createObjectURL(file);
    onChange({ screenshot: { url, name: file.name, size: file.size } });
    toast.success('Screenshot uploaded!');
  };

  const handleClearScreenshot = () => {
    if (data.screenshot?.url) URL.revokeObjectURL(data.screenshot.url);
    onChange({ screenshot: null });
  };

  return (
    <div className={`bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden transition-all duration-300 ${
      data.status !== 'pending' ? `ring-2 ${cfg.ring}` : ''
    }`}>
      {/* Card Header */}
      <div className={`px-6 py-4 ${cfg.header} bg-gradient-to-r from-slate-50 to-white flex items-start justify-between`}>
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${accentColor.bg}`}>
            {icon}
          </div>
          <div>
            <h3 className="font-bold text-slate-800 text-base">{title}</h3>
            <p className="text-xs text-slate-500 mt-0.5">Government Portal Verification</p>
          </div>
        </div>
        <span className={cfg.badge}>
          {cfg.icon}
          {cfg.label}
        </span>
      </div>

      {/* Card Body */}
      <div className="px-6 py-5 space-y-5">

        {/* Extracted Number */}
        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
            {numberLabel} (Extracted)
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={docNumber || 'Not extracted yet'}
              className="input bg-slate-50 text-slate-700 font-mono tracking-wider cursor-not-allowed select-all"
            />
            <div className="flex-shrink-0 p-2 rounded-lg bg-slate-100" title="Read-only — extracted by OCR">
              <BadgeInfo className="w-4 h-4 text-slate-400" />
            </div>
          </div>
          <p className="text-xs text-slate-400 mt-1 flex items-center gap-1">
            <Info className="w-3 h-3" /> Auto-extracted by OCR. Read-only.
          </p>
        </div>

        {/* Verify Button */}
        <a
          href={portalUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={`flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-xl font-semibold text-sm transition-all duration-150
            ${accentColor.btn} shadow-sm hover:shadow-md`}
        >
          <ExternalLink className="w-4 h-4" />
          {portalLabel}
        </a>

        {/* Screenshot Upload */}
        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Verification Screenshot
          </label>
          <ScreenshotDropzone
            screenshot={data.screenshot}
            onFileAccepted={handleScreenshotAccepted}
            onClear={handleClearScreenshot}
          />
        </div>

        {/* Status Selection */}
        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Verification Status
          </label>
          <div className="grid grid-cols-2 gap-2">
            {STATUS_OPTIONS.map((opt) => {
              const optCfg = statusConfig(opt.value);
              const isActive = data.status === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => handleStatusChange(opt.value)}
                  className={`flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs font-semibold border-2 transition-all duration-150 text-left
                    ${isActive
                      ? `${optCfg.badge.replace('inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ', '')} border-current scale-105 shadow-sm`
                      : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50'
                    }`}
                >
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isActive ? optCfg.dot : 'bg-slate-300'}`} />
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Officer & Timestamp */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
              Officer Name
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="Enter officer name"
                value={data.officerName}
                onChange={(e) => onChange({ officerName: e.target.value })}
                className="input pl-8"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
              Verification Timestamp
            </label>
            <div className="relative">
              <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <input
                type="text"
                readOnly
                value={data.timestamp || '—'}
                className="input pl-8 bg-slate-50 text-slate-600 cursor-not-allowed"
              />
            </div>
          </div>
        </div>

        {/* Remarks */}
        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
            Remarks
          </label>
          <textarea
            rows={2}
            placeholder="Optional remarks or observations…"
            value={data.remarks}
            onChange={(e) => onChange({ remarks: e.target.value })}
            className="input resize-none"
          />
        </div>

        {/* Reset button */}
        {data.status !== 'pending' && (
          <button
            type="button"
            onClick={() => onChange({ status: 'pending', timestamp: '', screenshot: null, officerName: '', remarks: '' })}
            className="w-full flex items-center justify-center gap-1.5 text-xs text-slate-400 hover:text-slate-600 py-1.5 transition-colors"
          >
            <RotateCcw className="w-3 h-3" /> Reset verification
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Summary Card ─────────────────────────────────────────────────────────────

function SummaryCard({ aadhaar, pan }) {
  const overall = overallStatus(aadhaar, pan);
  const aadhaarCfg = statusConfig(aadhaar.status);
  const panCfg     = statusConfig(pan.status);

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
      <div className="px-6 py-4 bg-gradient-to-r from-primary-900 to-primary-700 flex items-center gap-3">
        <ClipboardCheck className="w-5 h-5 text-blue-200" />
        <h3 className="font-bold text-white text-base">Government Verification Summary</h3>
      </div>

      <div className="px-6 py-5">
        {/* Individual statuses */}
        <div className="grid grid-cols-2 gap-4 mb-5">
          <div className="bg-slate-50 rounded-xl p-4 border border-slate-100">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Aadhaar</p>
            <span className={aadhaarCfg.badge}>
              {aadhaarCfg.icon}
              {aadhaarCfg.label}
            </span>
            {aadhaar.timestamp && (
              <p className="text-xs text-slate-400 mt-2 flex items-center gap-1">
                <Clock className="w-3 h-3" /> {aadhaar.timestamp}
              </p>
            )}
            {aadhaar.officerName && (
              <p className="text-xs text-slate-400 mt-1 flex items-center gap-1">
                <User className="w-3 h-3" /> {aadhaar.officerName}
              </p>
            )}
          </div>

          <div className="bg-slate-50 rounded-xl p-4 border border-slate-100">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">PAN</p>
            <span className={panCfg.badge}>
              {panCfg.icon}
              {panCfg.label}
            </span>
            {pan.timestamp && (
              <p className="text-xs text-slate-400 mt-2 flex items-center gap-1">
                <Clock className="w-3 h-3" /> {pan.timestamp}
              </p>
            )}
            {pan.officerName && (
              <p className="text-xs text-slate-400 mt-1 flex items-center gap-1">
                <User className="w-3 h-3" /> {pan.officerName}
              </p>
            )}
          </div>
        </div>

        {/* Overall status */}
        <div className={`rounded-xl p-4 border-2 flex items-center justify-between
          ${overall.label === 'Fully Verified'
            ? 'bg-emerald-50 border-emerald-200'
            : overall.label === 'Verification Failed'
            ? 'bg-red-50 border-red-200'
            : overall.label === 'Manual Review Required'
            ? 'bg-blue-50 border-blue-200'
            : overall.label === 'Partially Verified'
            ? 'bg-amber-50 border-amber-200'
            : 'bg-slate-50 border-slate-200'
          }`}>
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
              Overall Government Verification Status
            </p>
            <span className={overall.badge}>
              {overall.icon}
              {overall.label}
            </span>
          </div>
          <div className={`w-12 h-12 rounded-full flex items-center justify-center
            ${overall.label === 'Fully Verified'     ? 'bg-emerald-100'
            : overall.label === 'Verification Failed' ? 'bg-red-100'
            : overall.label === 'Manual Review Required' ? 'bg-blue-100'
            : 'bg-amber-100'}`}>
            {overall.label === 'Fully Verified'
              ? <CheckCircle2 className="w-6 h-6 text-emerald-600" />
              : overall.label === 'Verification Failed'
              ? <XCircle className="w-6 h-6 text-red-600" />
              : overall.label === 'Manual Review Required'
              ? <Eye className="w-6 h-6 text-blue-600" />
              : <Clock className="w-6 h-6 text-amber-600" />
            }
          </div>
        </div>

        {/* Screenshot checklist */}
        <div className="mt-4 pt-4 border-t border-slate-100">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Screenshot Checklist</p>
          <div className="space-y-2">
            {[
              { label: 'Aadhaar Screenshot', uploaded: !!aadhaar.screenshot },
              { label: 'PAN Screenshot',     uploaded: !!pan.screenshot },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-2">
                {item.uploaded
                  ? <CheckCircle2 className="w-4 h-4 text-emerald-500 flex-shrink-0" />
                  : <div className="w-4 h-4 rounded-full border-2 border-slate-300 flex-shrink-0" />
                }
                <span className={`text-sm ${item.uploaded ? 'text-slate-700' : 'text-slate-400'}`}>
                  {item.label}
                </span>
                {item.uploaded && (
                  <span className="text-xs text-emerald-600 font-medium ml-auto">Uploaded</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Initial State ────────────────────────────────────────────────────────────

const INITIAL_STATE = {
  status:      'pending',
  timestamp:   '',
  officerName: '',
  screenshot:  null,
  remarks:     '',
};

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function GovVerificationPage() {
  const { appId } = useParams();
  const navigate = useNavigate();
  const [aadhaar, setAadhaar] = useState({ ...INITIAL_STATE });
  const [pan, setPan]         = useState({ ...INITIAL_STATE });
  const [isSaving, setIsSaving] = useState(false);

  const [aadhaarNumber, setAadhaarNumber] = useState('XXXX XXXX XXXX');
  const [panNumber, setPanNumber]         = useState('ABCDE1234F');
  
  useEffect(() => {
    if (appId) {
      getApplication(appId).then(res => {
        const gov = res.data.gov_verification;
        if (gov) {
           setAadhaar(prev => ({ ...prev, status: gov.aadhaar_status || 'pending', officerName: gov.officer_name || '', remarks: gov.remarks || '' }));
           setPan(prev => ({ ...prev, status: gov.pan_status || 'pending', officerName: gov.officer_name || '', remarks: gov.remarks || '' }));
        }
      }).catch(err => console.error("Error fetching app", err));
    }
  }, [appId]);

  const patchAadhaar = (patch) => setAadhaar((prev) => ({ ...prev, ...patch }));
  const patchPan     = (patch) => setPan((prev) => ({ ...prev, ...patch }));

  const handleSave = async () => {
    if (!appId) return toast.error('No Application ID provided');
    setIsSaving(true);
    try {
      const payload = {
        aadhaar_status: aadhaar.status,
        pan_status: pan.status,
        tax_receipt_status: 'pending',
        officer_name: aadhaar.officerName || pan.officerName,
        timestamp: aadhaar.timestamp || pan.timestamp,
        remarks: aadhaar.remarks || pan.remarks,
        verification_screenshots: ''
      };
      await submitGovVerification(appId, payload);
      toast.success('Verification saved successfully!');
      navigate(`/applications`);
    } catch (error) {
      toast.error('Failed to save verification');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">

      {/* Page Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800 flex items-center gap-2.5">
            <ShieldCheck className="w-6 h-6 text-primary-600" />
            Government Verification
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Manually verify applicant documents via official government portals.
            Upload a screenshot as proof of verification before marking any document as Verified.
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="flex items-center gap-2 bg-primary-600 hover:bg-primary-700 text-white px-5 py-2.5 rounded-xl font-semibold transition-colors disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {isSaving ? 'Saving...' : 'Save to Application'}
        </button>
      </div>

      {/* Info Banner */}
      <div className="flex items-start gap-3 bg-blue-50 border border-blue-200 rounded-xl px-5 py-4">
        <AlertTriangle className="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-blue-800">
          <span className="font-semibold">Workflow: </span>
          Click the <strong>Verify</strong> button to open the official government portal in a new tab →
          verify the applicant details manually → take a screenshot → upload it here →
          then update the status.
        </div>
      </div>

      {/* Demo number override — simulates OCR pre-fill */}
      <details className="bg-slate-50 border border-dashed border-slate-300 rounded-xl">
        <summary className="px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide cursor-pointer select-none">
          ⚙ Simulate OCR-extracted numbers (demo)
        </summary>
        <div className="px-5 pb-4 pt-2 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Aadhaar Number</label>
            <input type="text" value={aadhaarNumber}
              onChange={(e) => setAadhaarNumber(e.target.value)} className="input font-mono" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">PAN Number</label>
            <input type="text" value={panNumber}
              onChange={(e) => setPanNumber(e.target.value)} className="input font-mono uppercase" />
          </div>
        </div>
      </details>

      {/* Verification Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <VerificationCard
          title="Aadhaar Verification"
          icon={<ShieldCheck className="w-5 h-5 text-orange-600" />}
          docNumber={aadhaarNumber}
          numberLabel="Aadhaar Number"
          portalUrl={AADHAAR_PORTAL}
          portalLabel="Verify Aadhaar on UIDAI Portal"
          accentColor={{
            bg:  'bg-orange-100',
            btn: 'bg-orange-600 hover:bg-orange-700 text-white',
          }}
          data={aadhaar}
          onChange={patchAadhaar}
        />

        <VerificationCard
          title="PAN Verification"
          icon={<ShieldCheck className="w-5 h-5 text-indigo-600" />}
          docNumber={panNumber}
          numberLabel="PAN Number"
          portalUrl={PAN_PORTAL}
          portalLabel="Verify PAN on Income Tax Portal"
          accentColor={{
            bg:  'bg-indigo-100',
            btn: 'bg-indigo-600 hover:bg-indigo-700 text-white',
          }}
          data={pan}
          onChange={patchPan}
        />
      </div>

      {/* Summary Card */}
      <SummaryCard aadhaar={aadhaar} pan={pan} />
    </div>
  );
}
