#!/usr/bin/env bash
# Deploy ESG Lens to Azure from the Bicep reference architecture (infra/azure/main.bicep).
#
# What you get: Container Apps + Azure OpenAI with keys disabled + Key Vault + ACR + Log Analytics,
# all wired through one user-assigned managed identity with least-privilege roles.
#
# Prerequisites: Azure CLI (`az login`), permission to create role assignments in the resource group
# (Owner or User Access Administrator), quota for the chosen models in the region.
#
#   bash deploy/azure/deploy.sh
#   AUTH_CLIENT_ID=<app registration id> bash deploy/azure/deploy.sh   # + Entra ID sign-in at the ingress
set -euo pipefail

RG="${RG:-rg-esg-lens}"
LOCATION="${LOCATION:-swedencentral}"   # pick a region where your models are available
PREFIX="${PREFIX:-esglens}"
TEMPLATE="infra/azure/main.bicep"

az group create -n "$RG" -l "$LOCATION" -o none

# The API token only ever lives in Key Vault. Re-running the script rotates it.
API_TOKEN="$(openssl rand -hex 24)"

echo "1/3 Provisioning infrastructure (placeholder image)..."
az deployment group create -g "$RG" -f "$TEMPLATE" -o none \
  -p prefix="$PREFIX" apiToken="$API_TOKEN"

ACR=$(az deployment group show -g "$RG" -n main --query properties.outputs.registryName.value -o tsv)

echo "2/3 Building the image in Azure Container Registry..."
az acr build -r "$ACR" -t esg-lens:latest . -o none

echo "3/3 Rolling out the real image..."
az deployment group create -g "$RG" -f "$TEMPLATE" -o none \
  -p prefix="$PREFIX" apiToken="$API_TOKEN" containerImage="$ACR.azurecr.io/esg-lens:latest"

if [[ -n "${AUTH_CLIENT_ID:-}" ]]; then
  # Second layer: only users of your Entra ID tenant reach the API (Container Apps built-in auth).
  az containerapp auth microsoft update -g "$RG" -n "$PREFIX-api" --client-id "$AUTH_CLIENT_ID" \
    --tenant-id "$(az account show --query tenantId -o tsv)" --yes -o none
  az containerapp auth update -g "$RG" -n "$PREFIX-api" --unauthenticated-client-action Return401 \
    --excluded-paths /health -o none
fi

URL=$(az deployment group show -g "$RG" -n main --query properties.outputs.appUrl.value -o tsv)
echo
echo "Deployed: $URL/docs"
echo "API token (shown once, keep it in your password manager): $API_TOKEN"
echo "Test:  curl -H \"Authorization: Bearer <token>\" $URL/documents"
echo "Clean up when done:  az group delete -n $RG"
