#!/usr/bin/env bash
# Deploy ESG Lens to Cloud Run with a dedicated service account calling Gemini on Vertex AI (no API key).
#
# Prerequisites: gcloud CLI logged in, a project with billing. Then: PROJECT=my-project bash deploy/gcp/deploy.sh
set -euo pipefail

PROJECT="${PROJECT:?set your GCP project id}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-esg-lens-api}"
SA_NAME="${SA_NAME:-esg-lens-run}"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
REPO="${REPO:-esg-lens}"

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com aiplatform.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com

# Least privilege: the runtime identity may only call Vertex AI models.
gcloud iam service-accounts create "$SA_NAME" --display-name "ESG Lens runtime" 2>/dev/null || true
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" \
  --role roles/aiplatform.user --condition None >/dev/null

gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" 2>/dev/null || true
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/esg-lens:latest"
gcloud builds submit --tag "$IMAGE" .

gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --cpu 1 --memory 2Gi --min-instances 0 --max-instances 3 --port 8080 \
  --set-env-vars ESG_PROVIDER=gemini,ESG_GCP_PROJECT="$PROJECT",ESG_GCP_LOCATION="${GEMINI_LOCATION:-global}",ESG_GCP_EMBEDDING_LOCATION="$REGION" \
  --no-allow-unauthenticated

echo "Deployed (private). Call it with an identity token:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')/health"
