import React, { useState, useCallback } from 'react';
import { useDropzone } from 'react-dropzone';

const API_BASE = 'http://127.0.0.1:8000/api/v1';

export default function App() {
  const [project, setProject] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [clips, setClips] = useState([]);
  const [progress, setProgress] = useState({ stage: '', percent: 0 });
  const [clipCount, setClipCount] = useState(3);
  const [equalSplit, setEqualSplit] = useState(false);

  const onDrop = useCallback(async (acceptedFiles) => {
    if (acceptedFiles.length === 0) return;
    const file = acceptedFiles[0];

    setIsProcessing(true);

    // Reset state from any previous upload
    setProject(null);
    setClips([]);
    setProgress({ stage: 'uploading', percent: 10 });

    const formData = new FormData();
    formData.append('file', file);

    const params = new URLSearchParams({
      clip_count: String(clipCount),
      platform: 'tiktok',
      profile: 'balanced',
      debug: 'false',
      equal_split: String(equalSplit),
    });
    const uploadUrl = `${API_BASE}/videos/?${params.toString()}`;

    try {
      console.log('[Orion] Uploading to', uploadUrl, { clipCount, equalSplit });
      // AbortController with 60-second timeout prevents indefinite hangs
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 60000);

      const response = await fetch(uploadUrl, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      console.log('[Orion] Upload response status', response.status);
      if (!response.ok) {
        console.error('[Orion] Upload HTTP error:', response.status, response.statusText);
        setIsProcessing(false);
        return;
      }

      const data = await response.json();
      console.log('[Orion] Upload response data', data);

      if (!data.project_id) {
        console.error('[Orion] Missing project_id in response:', data);
        setIsProcessing(false);
        return;
      }

      setProject(data);
      setProgress({ stage: 'processing', percent: 25 });

      // Poll for completion
      pollProgress(data.project_id);
    } catch (err) {
      if (err.name === 'AbortError') {
        console.error('[Orion] Upload timed out after 60s');
      } else {
        console.error('[Orion] Upload failed:', err);
      }
      setIsProcessing(false);
    }
  }, [clipCount, equalSplit]);

  const pollProgress = async (projectId) => {
    console.log('[Orion] Starting progress polling for', projectId);

    const doPoll = async () => {
      try {
        const url = `${API_BASE}/videos/${projectId}/progress`;
        console.log('[Orion] Polling', url);
        const res = await fetch(url);
        console.log('[Orion] Progress response status', res.status);
        if (!res.ok) {
          console.warn('[Orion] Progress request failed, will retry:', res.status);
          setTimeout(doPoll, 2000);
          return;
        }
        const data = await res.json();
        console.log('[Orion] Progress data', data);
        setProgress({ stage: data.stage || 'processing', percent: data.percent ?? 0 });

        if (data.percent >= 100 || data.status === 'completed') {
          fetchClips(projectId);
          setIsProcessing(false);
          console.log('[Orion] Processing complete');
          return;
        }
        // Continue polling
        setTimeout(doPoll, 2000);
      } catch (e) {
        console.error('[Orion] Poll error (retrying):', e.message);
        setTimeout(doPoll, 2000);
      }
    };

    doPoll();
  };

  const fetchClips = async (projectId) => {
    try {
      const url = `${API_BASE}/clips/${projectId}`;
      console.log('[Orion] Fetching clips from', url);
      const res = await fetch(url);
      console.log('[Orion] Clips response status', res.status);
      const data = await res.json();
      console.log('[Orion] Clips data', data);
      setClips(data.clips || []);
    } catch (e) {
      console.error('[Orion] Failed to fetch clips:', e);
    }
  };

  const handleReset = () => {
    setProject(null);
    setClips([]);
    setProgress({ stage: '', percent: 0 });
    setIsProcessing(false);
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/*': ['.mp4', '.mov', '.mkv', '.avi', '.webm'] },
    multiple: false,
  });

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1 style={styles.title}>Orion AI</h1>
        <p style={styles.subtitle}>AI Powered Video Understanding</p>
      </header>

      {!project && (
        <div style={{ width: '100%', maxWidth: '600px', marginBottom: '1.5rem' }}>
          <label style={{ color: '#aaa', fontSize: '0.9rem', marginBottom: '0.5rem', display: 'block' }}>
            Number of clips to generate:
          </label>
          <select
            value={clipCount}
            onChange={(e) => setClipCount(Number(e.target.value))}
            style={styles.select}
          >
            {[1,2,3,4,5,6,7,8,9,10].map(n => (
              <option key={n} value={n}>{n} clip{n > 1 ? 's' : ''}</option>
            ))}
          </select>
          <label style={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={equalSplit}
              onChange={(e) => setEqualSplit(e.target.checked)}
              style={styles.checkbox}
            />
            Cortes iguales (dividir el video en partes del mismo tamaño)
          </label>
        </div>
      )}

      {!project && (
        <div {...getRootProps()} style={{
          ...styles.dropzone,
          borderColor: isDragActive ? '#3b82f6' : '#333',
          background: isDragActive ? '#1a1a2e' : '#111',
        }}>
          <input {...getInputProps()} />
          <div style={styles.dropzoneContent}>
            <div style={styles.icon}>📹</div>
            <p style={styles.dropText}>
              {isDragActive ? 'Drop video here...' : 'Drag & drop a video, or click to select'}
            </p>
            <p style={styles.hint}>Supports MP4, MOV, MKV, AVI, WEBM</p>
          </div>
        </div>
      )}

      {isProcessing && (
        <div style={styles.progressContainer}>
          <p style={styles.progressText}>Processing: {progress.stage}</p>
          <div style={styles.progressBar}>
            <div style={{...styles.progressFill, width: `${progress.percent}%`}} />
          </div>
          <p style={styles.progressPercent}>{progress.percent}%</p>
        </div>
      )}

      {!isProcessing && project && clips.length === 0 && (
        <div style={styles.completedMessage}>
          <p style={styles.completedText}>✅ Processing complete!</p>
          <p style={styles.completedSubtext}>No clips were generated for this video.</p>
        </div>
      )}

      {clips.length > 0 && (
        <div style={styles.clipsContainer}>
          <div style={styles.clipsHeader}>
            <h2 style={styles.clipsTitle}>Generated Clips ({clips.length})</h2>
            <button onClick={handleReset} style={styles.resetBtn}>
              Subir otro video
            </button>
          </div>
          <div style={styles.clipsGrid}>
            {clips.map((clip) => (
              <div key={clip.clip_id} style={styles.clipCard}>
                <video
                  controls
                  preload="metadata"
                  style={styles.clipVideo}
                  src={`${API_BASE}/clips/${project?.project_id}/download/${clip.clip_id}`}
                />
                <div style={styles.clipInfo}>
                  <p style={styles.clipName}>{clip.filename}</p>
                  <a
                    href={`${API_BASE}/clips/${project?.project_id}/download/${clip.clip_id}`}
                    style={styles.downloadBtn}
                    download
                  >
                    Download
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    height: '100vh',
    padding: '2rem',
    overflow: 'hidden',
    boxSizing: 'border-box',
  },
  header: {
    textAlign: 'center',
    marginBottom: '2rem',
  },
  title: {
    fontSize: '2.5rem',
    fontWeight: 700,
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    marginBottom: '0.5rem',
  },
  subtitle: {
    color: '#888',
    fontSize: '1rem',
  },
  dropzone: {
    width: '100%',
    maxWidth: '600px',
    height: '300px',
    border: '2px dashed #333',
    borderRadius: '16px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    transition: 'all 0.3s',
    marginBottom: '2rem',
  },
  dropzoneContent: {
    textAlign: 'center',
  },
  icon: {
    fontSize: '3rem',
    marginBottom: '1rem',
  },
  dropText: {
    fontSize: '1.2rem',
    color: '#fff',
    marginBottom: '0.5rem',
  },
  hint: {
    fontSize: '0.85rem',
    color: '#666',
  },
  progressContainer: {
    width: '100%',
    maxWidth: '600px',
    marginBottom: '2rem',
  },
  progressText: {
    color: '#aaa',
    marginBottom: '0.5rem',
    textTransform: 'capitalize',
  },
  progressBar: {
    width: '100%',
    height: '8px',
    background: '#222',
    borderRadius: '4px',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #667eea, #764ba2)',
    transition: 'width 0.5s ease',
  },
  progressPercent: {
    color: '#888',
    fontSize: '0.85rem',
    marginTop: '0.5rem',
    textAlign: 'right',
  },
  clipsContainer: {
    width: '100%',
    maxWidth: '800px',
    flex: 1,
    overflowY: 'auto',
    paddingBottom: '2rem',
  },
  clipsHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '1rem',
  },
  clipsTitle: {
    fontSize: '1.5rem',
    color: '#fff',
    margin: 0,
  },
  resetBtn: {
    padding: '0.6rem 1.2rem',
    background: '#22c55e',
    color: '#fff',
    border: 'none',
    borderRadius: '8px',
    fontSize: '0.9rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
  clipsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
    gap: '1rem',
  },
  clipCard: {
    background: '#1a1a1a',
    borderRadius: '12px',
    overflow: 'hidden',
    border: '1px solid #333',
  },
  clipVideo: {
    width: '100%',
    height: 'auto',
    aspectRatio: '9/16',
    background: '#000',
    display: 'block',
  },
  clipInfo: {
    padding: '1rem',
  },
  clipName: {
    fontSize: '0.9rem',
    color: '#ddd',
    marginBottom: '0.75rem',
    wordBreak: 'break-all',
  },
  downloadBtn: {
    display: 'inline-block',
    padding: '0.5rem 1rem',
    background: '#3b82f6',
    color: '#fff',
    textDecoration: 'none',
    borderRadius: '6px',
    fontSize: '0.85rem',
    fontWeight: 500,
  },
  completedMessage: {
    width: '100%',
    maxWidth: '600px',
    textAlign: 'center',
    padding: '2rem',
    background: '#111',
    borderRadius: '12px',
    border: '1px solid #333',
    marginBottom: '2rem',
  },
  completedText: {
    fontSize: '1.3rem',
    color: '#4ade80',
    marginBottom: '0.5rem',
    fontWeight: 600,
  },
  completedSubtext: {
    fontSize: '0.9rem',
    color: '#888',
  },
  select: {
    width: '100%',
    padding: '0.75rem',
    background: '#1a1a1a',
    color: '#fff',
    border: '1px solid #333',
    borderRadius: '8px',
    fontSize: '1rem',
    cursor: 'pointer',
  },
  checkboxLabel: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    marginTop: '1rem',
    color: '#aaa',
    fontSize: '0.95rem',
    cursor: 'pointer',
  },
  checkbox: {
    width: '18px',
    height: '18px',
    cursor: 'pointer',
  },
};
