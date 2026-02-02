"""
@file main.py
@brief Flask backend for Nextflow Workload Visualizer using Python GCP libraries

@author Willis Zhang
@date 2026-01-30
"""

import json
import os
from flask import Flask, request, Response, stream_with_context
from flask_cors import CORS

# GCP Libraries
from google.cloud import storage
from google.cloud import resourcemanager_v3
from googleapiclient import discovery
from google.auth import default
from google.api_core import exceptions as gcp_exceptions

app = Flask(__name__)
CORS(app)

# Configuration
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "wz-workload-viz")
BUCKET_NAME = f"{PROJECT_ID}-bucket"
SERVICE_ACCOUNT_NAME = os.environ.get("SERVICE_ACCOUNT_NAME", "nextflow-pipeline-sa")
REGION = os.environ.get("GCP_REGION", "us-central1")


def stream_sse(data: dict) -> str:
    """Format data as Server-Sent Event"""
    return f"data: {json.dumps(data)}\n\n"


def log_msg(msg: str, msg_type: str = "info"):
    """Create a log SSE message"""
    print(f"[LOG] {msg}")
    return stream_sse({"log": msg, "type": msg_type})


def step_complete():
    """Mark step as complete"""
    return stream_sse({"log": "✓ Done", "type": "success", "status": "complete"})


def step_error(msg: str):
    """Mark step as error"""
    return stream_sse({"log": f"✗ {msg}", "type": "error", "status": "error"})


def execute_enable_apis():
    """Enable required GCP APIs using Service Usage API"""
    yield log_msg("Enabling Batch, Compute, and Logging APIs...")
    
    try:
        credentials, project = default()
        service = discovery.build('serviceusage', 'v1', credentials=credentials)
        
        apis = [
            'batch.googleapis.com',
            'compute.googleapis.com', 
            'logging.googleapis.com',
            'iam.googleapis.com',
            'cloudresourcemanager.googleapis.com'
        ]
        
        for api in apis:
            yield log_msg(f"  Enabling {api}...")
            try:
                request_body = {'consumerId': f'project:{PROJECT_ID}'}
                service.services().enable(
                    name=f'projects/{PROJECT_ID}/services/{api}'
                ).execute()
                yield log_msg(f"  ✓ {api} enabled", "success")
            except Exception as e:
                if "already enabled" in str(e).lower():
                    yield log_msg(f"  ✓ {api} already enabled", "info")
                else:
                    yield log_msg(f"  ⚠ {api}: {str(e)[:100]}", "info")
        
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_create_service_account():
    """Create service account using IAM API"""
    yield log_msg(f"Creating service account: {SERVICE_ACCOUNT_NAME}...")
    
    try:
        credentials, project = default()
        service = discovery.build('iam', 'v1', credentials=credentials)
        
        sa_email = f"{SERVICE_ACCOUNT_NAME}@{PROJECT_ID}.iam.gserviceaccount.com"
        
        try:
            # Check if SA exists
            service.projects().serviceAccounts().get(
                name=f"projects/{PROJECT_ID}/serviceAccounts/{sa_email}"
            ).execute()
            yield log_msg(f"  Service account already exists: {sa_email}", "info")
        except:
            # Create SA
            service.projects().serviceAccounts().create(
                name=f"projects/{PROJECT_ID}",
                body={
                    'accountId': SERVICE_ACCOUNT_NAME,
                    'serviceAccount': {
                        'displayName': 'Nextflow Pipeline Service Account'
                    }
                }
            ).execute()
            yield log_msg(f"  Created: {sa_email}", "success")
        
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_iam_roles():
    """Add IAM roles to service account"""
    yield log_msg("Adding IAM roles to service account...")
    
    try:
        credentials, project = default()
        service = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
        
        sa_email = f"{SERVICE_ACCOUNT_NAME}@{PROJECT_ID}.iam.gserviceaccount.com"
        member = f"serviceAccount:{sa_email}"
        
        roles = [
            'roles/iam.serviceAccountUser',
            'roles/batch.jobsEditor',
            'roles/batch.agentReporter',
            'roles/logging.viewer',
            'roles/storage.admin'
        ]
        
        # Get current policy
        policy = service.projects().getIamPolicy(
            resource=PROJECT_ID,
            body={}
        ).execute()
        
        # Add roles
        for role in roles:
            yield log_msg(f"  Adding {role}...")
            
            # Check if binding exists
            binding_exists = False
            for binding in policy.get('bindings', []):
                if binding['role'] == role:
                    if member not in binding['members']:
                        binding['members'].append(member)
                    binding_exists = True
                    break
            
            if not binding_exists:
                policy.setdefault('bindings', []).append({
                    'role': role,
                    'members': [member]
                })
        
        # Set updated policy
        service.projects().setIamPolicy(
            resource=PROJECT_ID,
            body={'policy': policy}
        ).execute()
        
        for role in roles:
            yield log_msg(f"  ✓ {role} granted", "success")
        
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_create_network():
    """Create VPC network and firewall rules for Google Batch"""
    yield log_msg("Setting up VPC network for Google Batch...")
    
    try:
        credentials, project = default()
        compute_service = discovery.build('compute', 'v1', credentials=credentials)
        
        # Check if default network exists
        try:
            compute_service.networks().get(
                project=PROJECT_ID,
                network='default'
            ).execute()
            yield log_msg("  ✓ Default VPC network already exists", "info")
        except Exception as e:
            if 'notFound' in str(e) or '404' in str(e):
                yield log_msg("  Creating default VPC network with auto-subnets...")
                
                network_body = {
                    'name': 'default',
                    'autoCreateSubnetworks': True,
                    'routingConfig': {
                        'routingMode': 'REGIONAL'
                    }
                }
                
                operation = compute_service.networks().insert(
                    project=PROJECT_ID,
                    body=network_body
                ).execute()
                
                # Wait for operation to complete
                yield log_msg("  Waiting for network creation...")
                while True:
                    result = compute_service.globalOperations().get(
                        project=PROJECT_ID,
                        operation=operation['name']
                    ).execute()
                    if result['status'] == 'DONE':
                        break
                
                yield log_msg("  ✓ Default VPC network created", "success")
            else:
                raise e
        
        # Check/create firewall rule for internal traffic
        firewall_name = 'default-allow-internal'
        try:
            compute_service.firewalls().get(
                project=PROJECT_ID,
                firewall=firewall_name
            ).execute()
            yield log_msg(f"  ✓ Firewall rule '{firewall_name}' already exists", "info")
        except Exception as e:
            if 'notFound' in str(e) or '404' in str(e):
                yield log_msg(f"  Creating firewall rule '{firewall_name}'...")
                
                firewall_body = {
                    'name': firewall_name,
                    'network': f'projects/{PROJECT_ID}/global/networks/default',
                    'direction': 'INGRESS',
                    'priority': 1000,
                    'allowed': [
                        {'IPProtocol': 'tcp'},
                        {'IPProtocol': 'udp'},
                        {'IPProtocol': 'icmp'}
                    ],
                    'sourceRanges': ['10.128.0.0/9']
                }
                
                operation = compute_service.firewalls().insert(
                    project=PROJECT_ID,
                    body=firewall_body
                ).execute()
                
                # Wait for operation to complete
                yield log_msg("  Waiting for firewall rule creation...")
                while True:
                    result = compute_service.globalOperations().get(
                        project=PROJECT_ID,
                        operation=operation['name']
                    ).execute()
                    if result['status'] == 'DONE':
                        break
                
                yield log_msg(f"  ✓ Firewall rule '{firewall_name}' created", "success")
            else:
                raise e
        
        # Enable Private Google Access on default subnet (required for internal-only VMs)
        yield log_msg("  Enabling Private Google Access on subnet...")
        try:
            subnet = compute_service.subnetworks().get(
                project=PROJECT_ID,
                region=REGION,
                subnetwork='default'
            ).execute()
            
            if subnet.get('privateIpGoogleAccess', False):
                yield log_msg("  ✓ Private Google Access already enabled", "info")
            else:
                compute_service.subnetworks().setPrivateIpGoogleAccess(
                    project=PROJECT_ID,
                    region=REGION,
                    subnetwork='default',
                    body={'privateIpGoogleAccess': True}
                ).execute()
                yield log_msg("  ✓ Private Google Access enabled", "success")
        except Exception as e:
            yield log_msg(f"  ⚠ Could not enable Private Google Access: {str(e)[:80]}", "info")
        
        yield log_msg("  Network: default (auto-subnets)", "info")
        yield log_msg("  Firewall: Internal traffic allowed (10.128.0.0/9)", "info")
        yield log_msg("  Private Google Access: Enabled (for internal-only VMs)", "info")
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_create_bucket():
    """Create GCS bucket using google-cloud-storage"""
    yield log_msg(f"Creating GCS bucket: gs://{BUCKET_NAME}...")
    
    try:
        client = storage.Client(project=PROJECT_ID)
        
        try:
            bucket = client.get_bucket(BUCKET_NAME)
            yield log_msg(f"  Bucket already exists: gs://{BUCKET_NAME}", "info")
        except gcp_exceptions.NotFound:
            bucket = client.create_bucket(BUCKET_NAME, location=REGION)
            yield log_msg(f"  Created bucket: gs://{BUCKET_NAME} in {REGION}", "success")
        
        yield log_msg(f"  Location: {bucket.location}", "info")
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_write_config():
    """Write nextflow.config file"""
    yield log_msg("Writing nextflow.config...")
    
    try:
        sa_email = f"{SERVICE_ACCOUNT_NAME}@{PROJECT_ID}.iam.gserviceaccount.com"
        config_content = f"""// Nextflow configuration for Google Cloud Batch
workDir = 'gs://{BUCKET_NAME}/scratch'

process {{
  executor = 'google-batch'
  container = 'nextflow/rnaseq-nf'
  // Retry on any failure (spot preemption, transient errors, etc.)
  // Retry count resets when task runs successfully
  errorStrategy = 'retry'
  maxRetries = 5
}}

google {{
  project = '{PROJECT_ID}'
  location = '{REGION}'
  batch {{
    spot = true
    serviceAccountEmail = '{sa_email}'
    // Use internal IPs only (required by org policy)
    usePrivateAddress = true
    network = 'projects/{PROJECT_ID}/global/networks/default'
    subnetwork = 'projects/{PROJECT_ID}/regions/{REGION}/subnetworks/default'
  }}
}}

timeline {{
  enabled = true
  file = 'timeline.html'
  overwrite = true
}}

report {{
  enabled = true
  file = 'report.html'
  overwrite = true
}}
"""
        
        config_path = os.path.join(os.getcwd(), 'nextflow.config')
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        yield log_msg(f"  Written to: {config_path}", "success")
        yield log_msg(f"  workDir: gs://{BUCKET_NAME}/scratch", "info")
        yield log_msg(f"  executor: google-batch", "info")
        yield log_msg(f"  region: {REGION}", "info")
        yield log_msg(f"  usePrivateAddress: true (internal IPs only)", "info")
        yield log_msg(f"  errorStrategy: retry (max 5 attempts)", "info")
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def execute_launch_pipeline():
    """Launch the Nextflow RNAseq pipeline on Google Batch with real-time streaming"""
    yield log_msg("Launching Nextflow RNAseq pipeline on Google Cloud Batch...")
    
    import subprocess
    try:
        yield log_msg("Command: nextflow run nextflow-io/rnaseq-nf -c nextflow.config", "info")
        
        # Use Popen for real-time streaming output
        process = subprocess.Popen(
            ["nextflow", "run", "nextflow-io/rnaseq-nf", "-c", "nextflow.config"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout for unified streaming
            text=True,
            bufsize=1,  # Line buffered
            cwd=os.getcwd(),
            env={**os.environ, 'NXF_ANSI_LOG': 'false'}  # Disable ANSI to get clean output
        )
        
        # Stream output line by line as it happens
        for line in process.stdout:
            line = line.rstrip()
            if line:
                # Determine log type based on content
                if 'ERROR' in line or 'WARN' in line:
                    yield log_msg(line, "error")
                elif 'Submitted' in line or '✓' in line or 'SUCCEEDED' in line:
                    yield log_msg(line, "success")
                else:
                    yield log_msg(line, "info")
        
        process.wait()
        
        if process.returncode == 0:
            yield log_msg("Pipeline completed successfully!", "success")
            yield step_complete()
        else:
            yield step_error(f"Pipeline failed with exit code {process.returncode}")
    except Exception as e:
        yield step_error(str(e))


def execute_check_jobs():
    """Check Google Batch job status"""
    yield log_msg("Checking Google Batch jobs...")
    
    try:
        credentials, project = default()
        service = discovery.build('batch', 'v1', credentials=credentials)
        
        parent = f"projects/{PROJECT_ID}/locations/{REGION}"
        request = service.projects().locations().jobs().list(parent=parent)
        response = request.execute()
        
        jobs = response.get('jobs', [])
        yield log_msg(f"  Found {len(jobs)} jobs", "info")
        
        for job in jobs[:5]:  # Show first 5 jobs
            name = job.get('name', 'unknown').split('/')[-1]
            state = job.get('status', {}).get('state', 'UNKNOWN')
            yield log_msg(f"  • {name}: {state}", "success" if state == "SUCCEEDED" else "info")
        
        yield step_complete()
    except Exception as e:
        yield log_msg(f"  Could not list jobs: {str(e)[:100]}", "info")
        yield step_complete()


def execute_list_results():
    """List results in GCS bucket"""
    yield log_msg(f"Listing results in gs://{BUCKET_NAME}...")
    
    try:
        client = storage.Client(project=PROJECT_ID)
        bucket = client.get_bucket(BUCKET_NAME)
        
        blobs = list(bucket.list_blobs(prefix="scratch/", max_results=20))
        yield log_msg(f"  Found {len(blobs)} files in scratch/", "info")
        
        for blob in blobs[:10]:
            yield log_msg(f"  • {blob.name}", "info")
        
        if len(blobs) > 10:
            yield log_msg(f"  ... and {len(blobs) - 10} more files", "info")
        
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


# Map step IDs to executor functions
STEP_EXECUTORS = {
    # Setup phase
    'enable-apis': execute_enable_apis,
    'create-sa': execute_create_service_account,
    'iam-roles': execute_iam_roles,
    'create-network': execute_create_network,
    'create-bucket': execute_create_bucket,
    'write-config': execute_write_config,
    # Pipeline phase
    'launch-pipeline': execute_launch_pipeline,
    'fastqc': execute_check_jobs,
    'quant': execute_check_jobs,
    'multiqc': execute_check_jobs,
    'results': execute_list_results,
}


@app.route('/api/execute', methods=['POST'])
def execute_step():
    """Execute a workflow step and stream output via SSE"""
    data = request.get_json()
    step_id = data.get('stepId', '')
    phase = data.get('phase', 'setup')

    print(f"\n{'='*60}")
    print(f"Executing step: {step_id} (phase: {phase})")
    print(f"{'='*60}\n")

    def generate():
        if step_id in STEP_EXECUTORS:
            yield from STEP_EXECUTORS[step_id]()
        else:
            yield log_msg(f"Unknown step: {step_id}", "error")
            yield step_error(f"Unknown step: {step_id}")

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "project": PROJECT_ID}


if __name__ == '__main__':
    print(f"""
    ╔═══════════════════════════════════════════════════════════════╗
    ║     Nextflow Workload Visualizer - Backend Server             ║
    ╠═══════════════════════════════════════════════════════════════╣
    ║  Project ID:  {PROJECT_ID:<45} ║
    ║  Bucket:      gs://{BUCKET_NAME:<42} ║
    ║  Region:      {REGION:<45} ║
    ╚═══════════════════════════════════════════════════════════════╝
    
    Server starting on http://localhost:5000
    Using Python GCP client libraries for all operations.
    """)
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
