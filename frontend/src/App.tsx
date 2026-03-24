import React, { useState } from 'react';
import { Upload, JobStatus, Player } from './components';

export const App: React.FC = () => {
  const [jobId, setJobId] = useState<string | null>(null);
  const [uploadComplete, setUploadComplete] = useState(false);

  return (
    <div style={{ background: '#0a0a0a', color: '#fff', minHeight: '100vh', padding: '40px' }}>
      <h1>AWS Video Transcoder</h1>
      
      {!jobId ? (
        <Upload onJobCreated={(id) => { setJobId(id); setUploadComplete(false); }} />
      ) : (
        <>
          {!uploadComplete ? (
            <JobStatus jobId={jobId} onComplete={() => setUploadComplete(true)} />
          ) : (
            <Player jobId={jobId} />
          )}
        </>
      )}
    </div>
  );
};

export default App;
