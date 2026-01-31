# Nextflow Workload Visualizer

Real-time visualization of Nextflow pipelines running on Google Cloud Batch.

---

## Step 0: Prerequisites

Install these tools before proceeding:

```bash
# Install gcloud CLI
# https://cloud.google.com/sdk/docs/install

# Install Node.js 18+
# https://nodejs.org/

# Install Python 3.11+
# https://www.python.org/

# Install Nextflow
curl -s https://get.nextflow.io | bash
sudo mv nextflow /usr/local/bin/
```

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/your-org/workload-viz-nextflow-batch.git
cd workload-viz-nextflow-batch
```

---

## Step 2: Create Your GCP Project

```bash
# Create a new GCP project
gcloud projects create YOUR_PROJECT_ID

# Find your billing account ID
gcloud billing accounts list

# Link billing to the project (required for APIs)
gcloud billing projects link YOUR_PROJECT_ID --billing-account=YOUR_BILLING_ACCOUNT_ID
```

---

## Step 3: Set Environment Variables

```bash
export GCP_PROJECT_ID="YOUR_PROJECT_ID"
```

---

## Step 4: Authenticate with GCP

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project $GCP_PROJECT_ID
```

---

## Step 5: Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

---

## Step 6: Set Up Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..
```

---

## Step 7: Start the Backend Server

In Terminal 1:

```bash
cd backend
source venv/bin/activate
export GCP_PROJECT_ID="YOUR_PROJECT_ID"
python main.py
```

---

## Step 8: Start the Frontend Server

In Terminal 2:

```bash
cd frontend
npm run dev
```

---

## Step 9: Open the Visualizer

Open **http://localhost:3000** in your browser.

Click **Get Started** → Click **Run Setup** to execute the GCP automation.

---

## Step 10: Run the Pipeline

After setup completes, the visualizer moves to pipeline execution phase.

Click **Run Pipeline** to execute the RNAseq Nextflow pipeline on Google Cloud Batch.

---

## Cleanup

```bash
gcloud storage rm -r gs://$GCP_PROJECT_ID-bucket
gcloud iam service-accounts delete nextflow-pipeline-sa@$GCP_PROJECT_ID.iam.gserviceaccount.com
gcloud projects delete $GCP_PROJECT_ID
```

---

## License

Apache License 2.0
