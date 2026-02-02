import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  ConnectionLineType,
  useReactFlow,
  ReactFlowProvider,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { SetupStepNode } from './SetupStepNode';
import { PipelineTaskNode } from './PipelineTaskNode';
import { ExecutionBox } from './ExecutionBox';
import { CostInfoTooltip } from './CostInfoTooltip';
import './WorkloadFlow.css';

const nodeTypes = {
  setupStep: SetupStepNode,
  pipelineTask: PipelineTaskNode,
};

const GCP_DIFFERENTIATORS = {
  'launch-pipeline': `🚀 Google Cloud Batch Advantages:
• Zero management overhead (unlike AWS Batch which requires EC2 template management)
• Native support for Spot VMs - massive cost savings for omics workloads
• Automatic retry with configurable error strategies
• Integrated with Cloud Logging for real-time monitoring`,
  'fastqc': `⚡ Google Batch Job Execution:
• No pre-warmed capacity needed - jobs start on demand
• Spot VMs can reduce costs by up to 91%
• Automatic VM provisioning and cleanup
• Per-second billing with no minimum runtime`,
  'quant': `📊 Compute Optimized for Bioinformatics:
• Access to latest Intel/AMD processors
• Up to 416 vCPUs per VM
• Local SSD for high-throughput I/O
• Batch automatically selects optimal machine types`,
  'multiqc': `📈 Efficient Resource Utilization:
• Tasks are scheduled across multiple VMs in parallel
• Preemptible instances for fault-tolerant workloads
• Integrated with Artifact Registry for container images
• Native Docker and Singularity support`,
  'results': `☁️ Google Cloud Storage Advantages:
• Time to first byte fastest among all storage classes
• Google's private network retrieves data immediately from all classes
• Data encrypted by default with configurable dual-region
• Customer-managed encryption keys available

🌐 Dual-Region Storage:
• Single bucket namespace spanning two regions
• Strong consistency with RTO of zero
• Other clouds require managing separate buckets with eventual consistency`,
};

const SETUP_STEPS = [
  { id: 'enable-apis', label: 'Enable APIs', command: 'Batch, Compute, Logging, IAM', icon: 'api' },
  { id: 'create-sa', label: 'Create Service Account', command: 'nextflow-pipeline-sa', icon: 'person' },
  { id: 'iam-roles', label: 'Add IAM Roles', command: '5 roles granted', icon: 'security' },
  { id: 'create-network', label: 'Create VPC Network', command: 'default + firewall', icon: 'lan' },
  { id: 'create-bucket', label: 'Create GCS Bucket', command: 'gs://wz-workload-viz-bucket', icon: 'cloud_upload' },
  { id: 'write-config', label: 'Write Nextflow Config', command: 'nextflow.config', icon: 'settings' },
];

const PIPELINE_STEPS = [
  { id: 'launch-pipeline', label: 'Launch Pipeline', command: 'nextflow run nextflow-io/rnaseq-nf', icon: 'play_arrow', tooltip: GCP_DIFFERENTIATORS['launch-pipeline'] },
  { id: 'fastqc', label: 'FASTQC', command: 'Quality Control', icon: 'biotech', tooltip: GCP_DIFFERENTIATORS['fastqc'], batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz' },
  { id: 'quant', label: 'QUANT', command: 'Quantification', icon: 'calculate', tooltip: GCP_DIFFERENTIATORS['quant'], batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz' },
  { id: 'multiqc', label: 'MULTIQC', command: 'Report Aggregation', icon: 'summarize', tooltip: GCP_DIFFERENTIATORS['multiqc'], batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz' },
  { id: 'results', label: 'GCS Results', command: 'gs://wz-workload-viz-bucket/scratch', icon: 'cloud_done', tooltip: GCP_DIFFERENTIATORS['results'], batchJobUrl: 'https://console.cloud.google.com/storage/browser/wz-workload-viz-bucket?project=wz-workload-viz' },
];

interface StepStatus {
  status: 'pending' | 'running' | 'complete' | 'error';
  logs: Array<{ timestamp: string; message: string; type: 'info' | 'success' | 'error' }>;
}

interface WorkloadFlowInnerProps {
  onComplete?: () => void;
}

// Inner component that uses useReactFlow
const WorkloadFlowInner: React.FC<WorkloadFlowInnerProps> = ({ onComplete }) => {
  const [stepStatuses, setStepStatuses] = useState<Record<string, StepStatus>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [currentPhase, setCurrentPhase] = useState<'setup' | 'pipeline'>('setup');
  const abortControllerRef = useRef<AbortController | null>(null);
  const { setViewport, fitView } = useReactFlow();

  // Layout constants - increased horizontal spacing
  const SETUP_START_X = 50;
  const SETUP_START_Y = 30;
  const SETUP_Y_GAP = 90;
  const CENTER_Y = 220;
  const PIPELINE_START_X = 500;
  const HORIZONTAL_GAP = 420; // More spacing
  const PARALLEL_Y_GAP = 130;

  // Zoom to setup nodes
  const zoomToSetup = useCallback(() => {
    setViewport({ x: 100, y: 0, zoom: 1.2 }, { duration: 800 });
  }, [setViewport]);

  // Zoom to pipeline nodes
  const zoomToPipeline = useCallback(() => {
    setViewport({ x: -350, y: 0, zoom: 0.9 }, { duration: 800 });
  }, [setViewport]);

  const generateNodes = useCallback((): Node[] => {
    const nodes: Node[] = [];

    // Setup nodes
    SETUP_STEPS.forEach((step, index) => {
      nodes.push({
        id: step.id,
        type: 'setupStep',
        position: { x: SETUP_START_X, y: SETUP_START_Y + index * SETUP_Y_GAP },
        data: {
          label: step.label,
          command: step.command,
          icon: step.icon,
          status: stepStatuses[step.id]?.status || 'pending',
          isSelected: selectedStep === step.id,
          onClick: () => setSelectedStep(step.id),
        },
      });
    });

    // Launch Pipeline
    nodes.push({
      id: 'launch-pipeline',
      type: 'pipelineTask',
      position: { x: PIPELINE_START_X, y: CENTER_Y },
      data: {
        label: 'Launch Pipeline',
        command: 'nextflow run nextflow-io/rnaseq-nf',
        icon: 'play_arrow',
        status: stepStatuses['launch-pipeline']?.status || 'pending',
        isSelected: selectedStep === 'launch-pipeline',
        onClick: () => setSelectedStep('launch-pipeline'),
        tooltip: GCP_DIFFERENTIATORS['launch-pipeline'],
      },
    });

    // Parallel tasks
    const parallelX = PIPELINE_START_X + HORIZONTAL_GAP;
    
    nodes.push({
      id: 'fastqc',
      type: 'pipelineTask',
      position: { x: parallelX, y: CENTER_Y - PARALLEL_Y_GAP },
      data: {
        label: 'FASTQC', command: 'Quality Control', icon: 'biotech',
        status: stepStatuses['fastqc']?.status || 'pending',
        isSelected: selectedStep === 'fastqc',
        onClick: () => setSelectedStep('fastqc'),
        tooltip: GCP_DIFFERENTIATORS['fastqc'],
        batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz',
      },
    });

    nodes.push({
      id: 'quant',
      type: 'pipelineTask',
      position: { x: parallelX, y: CENTER_Y },
      data: {
        label: 'QUANT', command: 'Quantification', icon: 'calculate',
        status: stepStatuses['quant']?.status || 'pending',
        isSelected: selectedStep === 'quant',
        onClick: () => setSelectedStep('quant'),
        tooltip: GCP_DIFFERENTIATORS['quant'],
        batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz',
      },
    });

    nodes.push({
      id: 'multiqc',
      type: 'pipelineTask',
      position: { x: parallelX, y: CENTER_Y + PARALLEL_Y_GAP },
      data: {
        label: 'MULTIQC', command: 'Report Aggregation', icon: 'summarize',
        status: stepStatuses['multiqc']?.status || 'pending',
        isSelected: selectedStep === 'multiqc',
        onClick: () => setSelectedStep('multiqc'),
        tooltip: GCP_DIFFERENTIATORS['multiqc'],
        batchJobUrl: 'https://console.cloud.google.com/batch/jobs?project=wz-workload-viz',
      },
    });

    // Results
    nodes.push({
      id: 'results',
      type: 'pipelineTask',
      position: { x: parallelX + HORIZONTAL_GAP, y: CENTER_Y },
      data: {
        label: 'GCS Results', command: 'gs://wz-workload-viz-bucket/scratch', icon: 'cloud_done',
        status: stepStatuses['results']?.status || 'pending',
        isSelected: selectedStep === 'results',
        onClick: () => setSelectedStep('results'),
        tooltip: GCP_DIFFERENTIATORS['results'],
        batchJobUrl: 'https://console.cloud.google.com/storage/browser/wz-workload-viz-bucket?project=wz-workload-viz',
      },
    });

    return nodes;
  }, [stepStatuses, selectedStep]);

  const generateEdges = useCallback((): Edge[] => {
    const edges: Edge[] = [];

    // Setup edges
    SETUP_STEPS.slice(0, -1).forEach((step, index) => {
      edges.push({
        id: `e-${step.id}-${SETUP_STEPS[index + 1].id}`,
        source: step.id, target: SETUP_STEPS[index + 1].id,
        sourceHandle: 'source-bottom', targetHandle: 'target-top',
        type: 'straight',
        animated: stepStatuses[step.id]?.status === 'complete',
        style: { stroke: stepStatuses[step.id]?.status === 'complete' ? '#4CAF50' : '#DADCE0', strokeWidth: 2 },
      });
    });

    // Write Config → Launch Pipeline
    edges.push({
      id: 'e-config-launch',
      source: 'write-config', target: 'launch-pipeline',
      sourceHandle: 'source-right', targetHandle: 'target-left',
      type: 'smoothstep',
      animated: stepStatuses['write-config']?.status === 'complete',
      style: { stroke: stepStatuses['write-config']?.status === 'complete' ? '#4CAF50' : '#DADCE0', strokeWidth: 2 },
    });

    // Launch → parallel tasks
    ['fastqc', 'quant', 'multiqc'].forEach(taskId => {
      edges.push({
        id: `e-launch-${taskId}`,
        source: 'launch-pipeline', target: taskId,
        sourceHandle: 'source-right', targetHandle: 'target-left',
        type: 'smoothstep',
        animated: stepStatuses['launch-pipeline']?.status === 'complete',
        style: { stroke: stepStatuses['launch-pipeline']?.status === 'complete' ? '#4CAF50' : '#DADCE0', strokeWidth: 2 },
      });
    });

    // Parallel tasks → Results
    ['fastqc', 'quant', 'multiqc'].forEach(taskId => {
      edges.push({
        id: `e-${taskId}-results`,
        source: taskId, target: 'results',
        sourceHandle: 'source-right', targetHandle: 'target-left',
        type: 'smoothstep',
        animated: stepStatuses[taskId]?.status === 'complete',
        style: { stroke: stepStatuses[taskId]?.status === 'complete' ? '#4CAF50' : '#DADCE0', strokeWidth: 2 },
      });
    });

    return edges;
  }, [stepStatuses]);

  const [nodes, setNodes, onNodesChange] = useNodesState(generateNodes());
  const [edges, setEdges, onEdgesChange] = useEdgesState(generateEdges());

  useEffect(() => {
    setNodes(generateNodes());
    setEdges(generateEdges());
  }, [stepStatuses, selectedStep, generateNodes, generateEdges, setNodes, setEdges]);

  const addLog = (stepId: string, message: string, type: 'info' | 'success' | 'error' = 'info') => {
    const timestamp = new Date().toLocaleTimeString('en-US', { hour12: false });
    setStepStatuses(prev => ({
      ...prev,
      [stepId]: { ...prev[stepId], logs: [...(prev[stepId]?.logs || []), { timestamp, message, type }] },
    }));
  };

  const runStep = async (stepId: string, stepLabel: string, signal: AbortSignal) => {
    setStepStatuses(prev => ({ ...prev, [stepId]: { status: 'running', logs: prev[stepId]?.logs || [] } }));
    setSelectedStep(stepId);
    addLog(stepId, `Starting: ${stepLabel}`, 'info');

    try {
      const response = await fetch('/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stepId, phase: currentPhase }),
        signal,
      });

      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error('No response body');

      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.substring(6));
              if (data.log) addLog(stepId, data.log, data.type || 'info');
              if (data.status === 'complete') {
                setStepStatuses(prev => ({ ...prev, [stepId]: { ...prev[stepId], status: 'complete' } }));
              } else if (data.status === 'error') {
                throw new Error(data.message);
              }
            } catch (e) {}
          }
        }
      }
      return true;
    } catch (error: any) {
      if (error.name === 'AbortError') { addLog(stepId, 'Aborted', 'error'); return false; }
      addLog(stepId, `Error: ${error.message}`, 'error');
      setStepStatuses(prev => ({ ...prev, [stepId]: { ...prev[stepId], status: 'error' } }));
      return false;
    }
  };

  const runAllSteps = async () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    setIsRunning(true);

    // Zoom to setup nodes
    zoomToSetup();

    // Run setup steps sequentially
    setCurrentPhase('setup');
    for (const step of SETUP_STEPS) {
      if (abortController.signal.aborted) break;
      const success = await runStep(step.id, step.label, abortController.signal);
      if (!success) break;
    }

    // Zoom to pipeline nodes
    zoomToPipeline();

    // Run pipeline steps
    setCurrentPhase('pipeline');
    
    let success = await runStep('launch-pipeline', 'Launch Pipeline', abortController.signal);
    if (!success) { setIsRunning(false); return; }

    const parallelPromises = ['fastqc', 'quant', 'multiqc'].map(taskId =>
      runStep(taskId, taskId.toUpperCase(), abortController.signal)
    );
    await Promise.all(parallelPromises);

    await runStep('results', 'GCS Results', abortController.signal);

    setIsRunning(false);
    if (onComplete) onComplete();
  };

  const stopExecution = () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    setIsRunning(false);
  };

  const selectedStepLogs = selectedStep ? stepStatuses[selectedStep]?.logs || [] : [];
  const allSteps = [...SETUP_STEPS, ...PIPELINE_STEPS];
  const selectedStepData = allSteps.find(s => s.id === selectedStep);

  return (
    <div className="workload-flow-wrapper">
      <div className="workload-header">
        <div className="header-left">
          <span className="material-symbols-outlined header-icon">cloud_sync</span>
          <div>
            <h1 className="title-large">Nextflow on Google Cloud Batch</h1>
            <p className="body-medium header-subtitle">Workload Visualization</p>
          </div>
        </div>
        <div className="header-right">
          {!isRunning ? (
            <button className="run-button" onClick={runAllSteps}>
              <span className="material-symbols-outlined">play_arrow</span>
              <span className="label-large">Run Workflow</span>
            </button>
          ) : (
            <button className="stop-button" onClick={stopExecution}>
              <span className="material-symbols-outlined">stop</span>
              <span className="label-large">Stop</span>
            </button>
          )}
        </div>
      </div>

      <div className="workload-content">
        <div className="flow-container">
          <CostInfoTooltip />
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            connectionLineType={ConnectionLineType.SmoothStep}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#E8EAED" gap={16} />
            <Controls />
          </ReactFlow>
        </div>

        <div className="logs-panel">
          <ExecutionBox
            title={selectedStepData?.label || 'Select a step'}
            command={selectedStepData?.command || ''}
            status={selectedStep ? stepStatuses[selectedStep]?.status || 'pending' : 'pending'}
            logs={selectedStepLogs}
          />
        </div>
      </div>
    </div>
  );
};

// Wrapper component with ReactFlowProvider
interface WorkloadFlowProps {
  onComplete?: () => void;
}

export const WorkloadFlow: React.FC<WorkloadFlowProps> = ({ onComplete }) => {
  return (
    <ReactFlowProvider>
      <WorkloadFlowInner onComplete={onComplete} />
    </ReactFlowProvider>
  );
};
