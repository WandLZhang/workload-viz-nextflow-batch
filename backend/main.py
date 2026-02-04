"""
@file main.py
@brief Flask backend for Nextflow Workload Visualizer using Python GCP libraries

@details This backend provides real GCP infrastructure provisioning including:
- VPC network and firewall configuration
- Vertex AI Workbench provisioning for researcher environments
- Google Batch job status polling for pipeline monitoring
- GCS bucket management for pipeline I/O

@author Willis Zhang
@date 2026-01-30
"""

import json
import os
import time
from flask import Flask, request, Response, stream_with_context, jsonify
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
ZONE = f"{REGION}-a"
WORKBENCH_INSTANCE_NAME = os.environ.get("WORKBENCH_INSTANCE_NAME", "nextflow-researcher-workbench")


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
            'cloudresourcemanager.googleapis.com',
            'orgpolicy.googleapis.com'  
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


def execute_configure_org_policies():
    """Configure org policies for Google Batch compatibility"""
    yield log_msg("Configuring org policies for Batch compatibility...")
    
    try:
        credentials, project = default()
        
        # Use orgpolicy API to set project-level overrides
        # This requires orgpolicy.policy.set permission
        from google.cloud import orgpolicy_v2
        
        client = orgpolicy_v2.OrgPolicyClient(credentials=credentials)
        
        # 1. Boolean policy: Disable Shielded VM requirement
        yield log_msg("  Setting compute.requireShieldedVm...")
        try:
            policy = orgpolicy_v2.Policy()
            policy.name = f"projects/{PROJECT_ID}/policies/compute.requireShieldedVm"
            policy.spec = orgpolicy_v2.PolicySpec()
            policy.spec.rules = [
                orgpolicy_v2.PolicySpec.PolicyRule(enforce=False)
            ]
            request = orgpolicy_v2.UpdatePolicyRequest(policy=policy)
            client.update_policy(request=request)
            yield log_msg("  ✓ compute.requireShieldedVm - exception granted", "success")
        except Exception as e:
            if 'already' in str(e).lower() or 'no change' in str(e).lower():
                yield log_msg("  ✓ compute.requireShieldedVm - already configured", "info")
            else:
                yield log_msg(f"  ⚠ compute.requireShieldedVm - {str(e)[:60]}", "info")
        
        # 2. List policy: Allow Batch image projects
        yield log_msg("  Setting compute.trustedImageProjects...")
        try:
            policy = orgpolicy_v2.Policy()
            policy.name = f"projects/{PROJECT_ID}/policies/compute.trustedImageProjects"
            policy.spec = orgpolicy_v2.PolicySpec()
            policy.spec.rules = [
                orgpolicy_v2.PolicySpec.PolicyRule(
                    values=orgpolicy_v2.PolicySpec.PolicyRule.StringValues(
                        allowed_values=[
                            "projects/batch-custom-image",
                            "projects/cos-cloud",
                            "projects/debian-cloud",
                            "projects/ubuntu-os-cloud"
                        ]
                    )
                )
            ]
            request = orgpolicy_v2.UpdatePolicyRequest(policy=policy)
            client.update_policy(request=request)
            yield log_msg("  ✓ compute.trustedImageProjects - batch images allowed", "success")
        except Exception as e:
            if 'already' in str(e).lower() or 'no change' in str(e).lower():
                yield log_msg("  ✓ compute.trustedImageProjects - already configured", "info")
            else:
                yield log_msg(f"  ⚠ compute.trustedImageProjects - {str(e)[:60]}", "info")
        
        yield log_msg("  Note: usePrivateAddress=true handles external IP constraint", "info")
        yield step_complete()
    except ImportError:
        yield log_msg("  ⚠ google-cloud-org-policy not installed, skipping", "info")
        yield log_msg("  Org policies may need manual configuration", "info")
        yield step_complete()
    except Exception as e:
        yield log_msg(f"  ⚠ Could not configure org policies: {str(e)[:80]}", "info")
        yield log_msg("  You may need to configure manually if Batch jobs fail", "info")
        yield step_complete()


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


def execute_provision_workbench():
    """
    Provision a Vertex AI Workbench instance for researchers.
    Uses the Notebooks API (notebooks.googleapis.com) to create a managed notebook instance.
    If instance already exists, returns the URL to access it.
    """
    yield log_msg(f"Provisioning Vertex AI Workbench: {WORKBENCH_INSTANCE_NAME}...")
    
    try:
        credentials, project = default()
        
        # First, enable the Notebooks API if not already enabled
        yield log_msg("  Enabling notebooks.googleapis.com API...")
        try:
            service_usage = discovery.build('serviceusage', 'v1', credentials=credentials)
            service_usage.services().enable(
                name=f'projects/{PROJECT_ID}/services/notebooks.googleapis.com'
            ).execute()
            yield log_msg("  ✓ Notebooks API enabled", "success")
        except Exception as e:
            if "already enabled" in str(e).lower():
                yield log_msg("  ✓ Notebooks API already enabled", "info")
            else:
                yield log_msg(f"  ⚠ Notebooks API: {str(e)[:80]}", "info")
        
        # Build the Notebooks API client
        notebooks_service = discovery.build('notebooks', 'v1', credentials=credentials)
        
        instance_name = f"projects/{PROJECT_ID}/locations/{ZONE}/instances/{WORKBENCH_INSTANCE_NAME}"
        workbench_url = f"https://console.cloud.google.com/vertex-ai/workbench/instances?project={PROJECT_ID}"
        jupyter_url = None
        
        # Check if instance already exists
        try:
            yield log_msg(f"  Checking for existing instance...")
            instance = notebooks_service.projects().locations().instances().get(
                name=instance_name
            ).execute()
            
            state = instance.get('state', 'UNKNOWN')
            yield log_msg(f"  ✓ Workbench instance already exists (state: {state})", "info")
            
            # Get the proxy URI for JupyterLab access
            if 'proxyUri' in instance:
                jupyter_url = instance['proxyUri']
                yield log_msg(f"  JupyterLab URL: {jupyter_url}", "success")
            
            # Send the workbench URL for frontend to display
            yield stream_sse({
                "log": f"Workbench ready: {WORKBENCH_INSTANCE_NAME}",
                "type": "success",
                "workbenchUrl": workbench_url,
                "jupyterUrl": jupyter_url,
                "instanceName": WORKBENCH_INSTANCE_NAME,
                "status": "complete"
            })
            return
            
        except Exception as e:
            if 'notFound' not in str(e).lower() and '404' not in str(e):
                raise e
            yield log_msg(f"  Instance not found, creating new workbench...", "info")
        
        # Create the Workbench instance
        sa_email = f"{SERVICE_ACCOUNT_NAME}@{PROJECT_ID}.iam.gserviceaccount.com"
        
        # Startup script to install Nextflow and configure the environment
        startup_script = f'''#!/bin/bash
# Install Java (required for Nextflow)
apt-get update && apt-get install -y default-jdk

# Install Nextflow
curl -s https://get.nextflow.io | bash
mv nextflow /usr/local/bin/

# Create researcher workspace
mkdir -p /home/jupyter/nextflow-workspace
cd /home/jupyter/nextflow-workspace

# Create a sample nextflow.config
cat > nextflow.config << 'EOF'
workDir = 'gs://{BUCKET_NAME}/scratch'

process {{
  executor = 'google-batch'
  container = 'nextflow/rnaseq-nf'
  errorStrategy = 'retry'
  maxRetries = 5
  machineType = 'n1-standard-1'
  disk = '30 GB'
}}

google {{
  project = '{PROJECT_ID}'
  location = '{REGION}'
  batch {{
    spot = true
    serviceAccountEmail = '{sa_email}'
    usePrivateAddress = true
    network = 'projects/{PROJECT_ID}/global/networks/default'
    subnetwork = 'projects/{PROJECT_ID}/regions/{REGION}/subnetworks/default'
  }}
}}
EOF

# Create a getting started notebook
cat > /home/jupyter/nextflow-workspace/Getting_Started_RNAseq.ipynb << 'NOTEBOOK'
{{
  "cells": [
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["# 🧬 Getting Started with Nextflow on Google Cloud Batch\\n", "\\n", "This notebook walks you through running an RNAseq pipeline using Nextflow on Google Cloud Batch."]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 1: Verify Environment"]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["!nextflow -version\\n", "!gcloud config get-value project"]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 2: Create GCS Bucket for Pipeline I/O"]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["BUCKET_NAME = '{BUCKET_NAME}'\\n", "!gcloud storage buckets describe gs://$BUCKET_NAME || gcloud storage buckets create gs://$BUCKET_NAME --location={REGION}"]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 3: Launch RNAseq Pipeline"]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["%%bash\\n", "cd /home/jupyter/nextflow-workspace\\n", "nextflow run nextflow-io/rnaseq-nf -c nextflow.config"]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 4: Monitor Batch Jobs"]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["!gcloud batch jobs list --location={REGION} --filter='name~nf-' --format='table(name.basename(),status.state,createTime)'"]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 5: View Results"]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["!gcloud storage ls gs://{BUCKET_NAME}/scratch/"]
    }},
    {{
      "cell_type": "markdown",
      "metadata": {{}},
      "source": ["## Step 6: Analyze Results\\n", "\\n", "Load and analyze the quantification results."]
    }},
    {{
      "cell_type": "code",
      "execution_count": null,
      "metadata": {{}},
      "outputs": [],
      "source": ["import pandas as pd\\n", "# Download results locally\\n", "!gcloud storage cp -r gs://{BUCKET_NAME}/scratch/results/ ./results/\\n", "# View MultiQC report (open in browser)\\n", "print('View MultiQC report: results/multiqc_report.html')"]
    }}
  ],
  "metadata": {{
    "kernelspec": {{
      "display_name": "Python 3",
      "language": "python",
      "name": "python3"
    }}
  }},
  "nbformat": 4,
  "nbformat_minor": 4
}}
NOTEBOOK

chown -R jupyter:jupyter /home/jupyter/nextflow-workspace
'''

        instance_body = {
            'vmImage': {
                'project': 'deeplearning-platform-release',
                'imageFamily': 'common-cpu-notebooks'
            },
            'machineType': 'n1-standard-4',
            'serviceAccount': sa_email,
            'network': f'projects/{PROJECT_ID}/global/networks/default',
            'subnet': f'projects/{PROJECT_ID}/regions/{REGION}/subnetworks/default',
            'noPublicIp': True,  # Use internal IP only (org policy compliance)
            'metadata': {
                'startup-script': startup_script,
                'proxy-mode': 'service_account'
            },
            'postStartupScript': startup_script
        }
        
        yield log_msg("  Creating Workbench instance (this takes 3-5 minutes)...", "info")
        yield log_msg(f"  Machine: n1-standard-4, Zone: {ZONE}", "info")
        yield log_msg(f"  Network: default (no public IP)", "info")
        
        operation = notebooks_service.projects().locations().instances().create(
            parent=f"projects/{PROJECT_ID}/locations/{ZONE}",
            instanceId=WORKBENCH_INSTANCE_NAME,
            body=instance_body
        ).execute()
        
        operation_name = operation.get('name')
        yield log_msg(f"  Operation started: {operation_name.split('/')[-1]}", "info")
        
        # Poll for operation completion
        max_wait = 600  # 10 minutes max
        poll_interval = 15
        elapsed = 0
        
        while elapsed < max_wait:
            op_result = notebooks_service.projects().locations().operations().get(
                name=operation_name
            ).execute()
            
            if op_result.get('done'):
                if 'error' in op_result:
                    yield step_error(f"Failed: {op_result['error'].get('message', 'Unknown error')}")
                    return
                
                yield log_msg("  ✓ Workbench instance created successfully!", "success")
                
                # Get the instance details
                instance = notebooks_service.projects().locations().instances().get(
                    name=instance_name
                ).execute()
                
                if 'proxyUri' in instance:
                    jupyter_url = instance['proxyUri']
                    yield log_msg(f"  JupyterLab URL: {jupyter_url}", "success")
                
                yield stream_sse({
                    "log": f"Workbench ready: {WORKBENCH_INSTANCE_NAME}",
                    "type": "success",
                    "workbenchUrl": workbench_url,
                    "jupyterUrl": jupyter_url,
                    "instanceName": WORKBENCH_INSTANCE_NAME,
                    "status": "complete"
                })
                return
            
            elapsed += poll_interval
            yield log_msg(f"  Provisioning... ({elapsed}s elapsed)", "info")
            time.sleep(poll_interval)
        
        yield log_msg("  ⚠ Workbench still provisioning (check console)", "info")
        yield stream_sse({
            "log": f"Workbench provisioning in progress",
            "type": "info",
            "workbenchUrl": workbench_url,
            "instanceName": WORKBENCH_INSTANCE_NAME,
            "status": "complete"
        })
        
    except Exception as e:
        print(f"[ERROR] Workbench provisioning failed: {str(e)}")
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
// Configured for org policy compliance (internal IPs, shielded VMs)
workDir = 'gs://{BUCKET_NAME}/scratch'

process {{
  executor = 'google-batch'
  container = 'nextflow/rnaseq-nf'
  // Retry on any failure (spot preemption, transient errors, etc.)
  // Retry count resets when task runs successfully
  errorStrategy = 'retry'
  maxRetries = 5
  machineType = 'n1-standard-1'
  disk = '30 GB'
}}

google {{
  project = '{PROJECT_ID}'
  location = '{REGION}'
  batch {{
    spot = true
    serviceAccountEmail = '{sa_email}'
    // Use internal IPs only (required by org policy: compute.vmExternalIpAccess)
    usePrivateAddress = true
    network = 'projects/{PROJECT_ID}/global/networks/default'
    subnetwork = 'projects/{PROJECT_ID}/regions/{REGION}/subnetworks/default'
    // Note: Shielded VM is required by org policy: compute.requireShieldedVm
    // The nf-google plugin uses Batch API which should respect project-level defaults
    installGpuDrivers = false
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
        yield log_msg(f"  usePrivateAddress: true (org policy compliance)", "info")
        yield log_msg(f"  errorStrategy: retry (max 5 attempts)", "info")
        yield log_msg(f"  spot: true (cost optimization)", "info")
        yield step_complete()
    except Exception as e:
        yield step_error(str(e))


def task_update(task_id: str, status: str, message: str = ""):
    """Send a task-specific status update SSE event"""
    return stream_sse({
        "type": "task_update",
        "task": task_id,
        "status": status,
        "message": message
    })


def parse_task_from_line(line: str) -> tuple:
    """
    Parse Nextflow output line for task status updates.
    Returns (task_id, status, message) or (None, None, None) if not a task line.
    
    Patterns to detect:
    - "Submitted process > RNASEQ:FASTQC" → fastqc, submitted
    - "[xx/yyyyyy] process > RNASEQ:FASTQC (sample) [100%]" → fastqc, complete
    - "COMPLETED" lines
    - "ERROR" lines
    """
    import re
    
    # Task name mapping from Nextflow process names to our UI task IDs
    task_mapping = {
        'INDEX': 'index',
        'FASTQC': 'fastqc',
        'QUANT': 'quant',
        'MULTIQC': 'multiqc',
    }
    
    # Pattern: "Submitted process > RNASEQ:TASKNAME"
    submitted_match = re.search(r'Submitted process > (?:RNASEQ:)?(\w+)', line)
    if submitted_match:
        task_name = submitted_match.group(1).upper()
        if task_name in task_mapping:
            return task_mapping[task_name], 'running', f'{task_name} submitted to Batch'
    
    # Pattern: process completed (shows percentage)
    # [db/8ab432] RNASEQ:INDEX (sample) [100%]
    complete_match = re.search(r'\[[\w/]+\]\s+(?:RNASEQ:)?(\w+)\s+\([^)]+\)\s+\[100%\]', line)
    if complete_match:
        task_name = complete_match.group(1).upper()
        if task_name in task_mapping:
            return task_mapping[task_name], 'complete', f'{task_name} completed'
    
    # Pattern: COMPLETED state from status
    if 'status: COMPLETED' in line or 'SUCCEEDED' in line:
        for name, task_id in task_mapping.items():
            if name in line:
                return task_id, 'complete', f'{name} completed'
    
    # Pattern: ERROR or FAILED
    if 'ERROR' in line or 'FAILED' in line:
        for name, task_id in task_mapping.items():
            if name in line:
                return task_id, 'error', f'{name} failed'
    
    return None, None, None


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
                # Parse for task-specific status updates
                task_id, task_status, task_message = parse_task_from_line(line)
                if task_id:
                    yield task_update(task_id, task_status, task_message)
                
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
            # Mark all remaining tasks as complete
            for task_id in ['index', 'fastqc', 'quant', 'multiqc']:
                yield task_update(task_id, 'complete', f'{task_id.upper()} completed')
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
    # Infrastructure setup phase (platform team)
    'enable-apis': execute_enable_apis,
    'create-sa': execute_create_service_account,
    'iam-roles': execute_iam_roles,
    'org-policies': execute_configure_org_policies,
    'create-network': execute_create_network,
    # Researcher environment provisioning
    'provision-workbench': execute_provision_workbench,
    # Researcher workflow (triggered from notebook cells, but we visualize)
    'storage-bucket': execute_create_bucket,
    'write-config': execute_write_config,
    # Pipeline execution
    'launch-pipeline': execute_launch_pipeline,
    'index': execute_check_jobs,
    'fastqc': execute_check_jobs,
    'quant': execute_check_jobs,
    'multiqc': execute_check_jobs,
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


@app.route('/api/poll-jobs', methods=['GET'])
def poll_jobs():
    """
    Poll Google Batch for job status - returns current status of all nf-* jobs.
    Used by frontend to animate pipeline progress in real-time.
    
    Returns JSON with job statuses mapped to task IDs (index, fastqc, quant, multiqc).
    """
    print(f"\n[POLL] Polling Batch jobs...")
    
    try:
        credentials, project = default()
        service = discovery.build('batch', 'v1', credentials=credentials)
        
        parent = f"projects/{PROJECT_ID}/locations/{REGION}"
        request = service.projects().locations().jobs().list(parent=parent)
        response = request.execute()
        
        jobs = response.get('jobs', [])
        
        # Filter to only nf-* jobs (Nextflow jobs)
        nf_jobs = [j for j in jobs if j.get('name', '').split('/')[-1].startswith('nf-')]
        
        # Sort by creation time (newest first)
        nf_jobs.sort(key=lambda j: j.get('createTime', ''), reverse=True)
        
        # Build response with job details
        job_list = []
        task_statuses = {
            'index': 'pending',
            'fastqc': 'pending', 
            'quant': 'pending',
            'multiqc': 'pending',
            'results': 'pending'
        }
        
        # Map job names to tasks based on Nextflow naming patterns
        # Nextflow creates jobs like: nf-RNASEQ_INDEX-xxxxx, nf-RNASEQ_FASTQC-xxxxx, etc.
        for job in nf_jobs[:20]:  # Look at recent 20 jobs
            job_name = job.get('name', '').split('/')[-1]
            state = job.get('status', {}).get('state', 'UNKNOWN')
            create_time = job.get('createTime', '')
            
            # Calculate runtime if available
            runtime_seconds = None
            status_events = job.get('status', {}).get('statusEvents', [])
            if status_events:
                # Find start and end times
                start_time = None
                end_time = None
                for event in status_events:
                    event_time = event.get('eventTime', '')
                    if 'RUNNING' in event.get('description', '').upper():
                        start_time = event_time
                    if state in ['SUCCEEDED', 'FAILED']:
                        end_time = event_time
                
                if start_time and end_time:
                    from datetime import datetime
                    try:
                        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        runtime_seconds = int((end - start).total_seconds())
                    except:
                        pass
            
            job_info = {
                'name': job_name,
                'state': state,
                'createTime': create_time,
                'runtimeSeconds': runtime_seconds
            }
            job_list.append(job_info)
            
            # Map to task IDs
            job_name_upper = job_name.upper()
            if 'INDEX' in job_name_upper:
                task_statuses['index'] = 'complete' if state == 'SUCCEEDED' else ('running' if state == 'RUNNING' else 'pending')
            elif 'FASTQC' in job_name_upper:
                task_statuses['fastqc'] = 'complete' if state == 'SUCCEEDED' else ('running' if state == 'RUNNING' else 'pending')
            elif 'QUANT' in job_name_upper:
                task_statuses['quant'] = 'complete' if state == 'SUCCEEDED' else ('running' if state == 'RUNNING' else 'pending')
            elif 'MULTIQC' in job_name_upper:
                task_statuses['multiqc'] = 'complete' if state == 'SUCCEEDED' else ('running' if state == 'RUNNING' else 'pending')
        
        # Check if all pipeline tasks are complete → results are ready
        pipeline_tasks = ['index', 'fastqc', 'quant', 'multiqc']
        all_complete = all(task_statuses[t] == 'complete' for t in pipeline_tasks)
        if all_complete:
            task_statuses['results'] = 'complete'
        elif any(task_statuses[t] in ['running', 'complete'] for t in pipeline_tasks):
            task_statuses['results'] = 'running'
        
        response_data = {
            'jobs': job_list,
            'taskStatuses': task_statuses,
            'totalJobs': len(nf_jobs),
            'pipelineComplete': all_complete
        }
        
        print(f"[POLL] Found {len(nf_jobs)} Nextflow jobs, task statuses: {task_statuses}")
        return jsonify(response_data)
        
    except Exception as e:
        print(f"[POLL ERROR] {str(e)}")
        return jsonify({
            'error': str(e),
            'jobs': [],
            'taskStatuses': {
                'index': 'pending',
                'fastqc': 'pending',
                'quant': 'pending',
                'multiqc': 'pending',
                'results': 'pending'
            }
        }), 500


@app.route('/api/workbench-status', methods=['GET'])
def get_workbench_status():
    """
    Get the current status and URL of the Vertex AI Workbench instance.
    Used by frontend to display link to workbench and check if it's ready.
    """
    print(f"\n[WORKBENCH] Checking workbench status...")
    
    try:
        credentials, project = default()
        notebooks_service = discovery.build('notebooks', 'v1', credentials=credentials)
        
        instance_name = f"projects/{PROJECT_ID}/locations/{ZONE}/instances/{WORKBENCH_INSTANCE_NAME}"
        
        try:
            instance = notebooks_service.projects().locations().instances().get(
                name=instance_name
            ).execute()
            
            state = instance.get('state', 'UNKNOWN')
            proxy_uri = instance.get('proxyUri', None)
            
            workbench_url = f"https://console.cloud.google.com/vertex-ai/workbench/instances?project={PROJECT_ID}"
            
            response_data = {
                'exists': True,
                'state': state,
                'instanceName': WORKBENCH_INSTANCE_NAME,
                'workbenchUrl': workbench_url,
                'jupyterUrl': proxy_uri,
                'ready': state == 'ACTIVE'
            }
            
            print(f"[WORKBENCH] Instance state: {state}, ready: {state == 'ACTIVE'}")
            return jsonify(response_data)
            
        except Exception as e:
            if 'notFound' in str(e).lower() or '404' in str(e):
                return jsonify({
                    'exists': False,
                    'state': 'NOT_FOUND',
                    'instanceName': WORKBENCH_INSTANCE_NAME,
                    'ready': False
                })
            raise e
            
    except Exception as e:
        print(f"[WORKBENCH ERROR] {str(e)}")
        return jsonify({
            'error': str(e),
            'exists': False,
            'ready': False
        }), 500


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
