// ESG Lens on Azure: reference architecture, keyless end to end.
//
//   Container Apps (API) --managed identity--> Azure OpenAI (Microsoft Foundry), local auth disabled
//                        --managed identity--> Key Vault (API token secret)
//                        --managed identity--> Container Registry (image pull)
//   Logs -> Log Analytics
//
// Deploy: bash infra/azure/deploy.sh   (see that script for the full sequence)

targetScope = 'resourceGroup'

@description('Azure region. Check model availability for this region.')
param location string = resourceGroup().location

@description('Short prefix used in resource names (lowercase letters and digits).')
@minLength(3)
@maxLength(12)
param prefix string = 'esglens'

@description('Container image. The first deployment uses a placeholder; deploy.sh then pushes the real one.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Chat model deployed in Azure OpenAI.')
param chatModelName string = 'gpt-5.4-mini'
param chatModelVersion string = '2026-03-17'
param chatCapacity int = 30

@description('Embedding model deployed in Azure OpenAI.')
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingCapacity int = 30

@description('Bearer token required by the API. Stored only in Key Vault.')
@secure()
param apiToken string

var suffix = uniqueString(resourceGroup().id)
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

// Identity ------------------------------------------------------------------------------------------
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
}

// Observability -------------------------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// Registry ------------------------------------------------------------------------------------------
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}acr${suffix}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

// Secrets -------------------------------------------------------------------------------------------
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv-${take(suffix, 8)}'
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource apiTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'api-token'
  properties: { value: apiToken }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, roles.keyVaultSecretsUser)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

// Models (Azure OpenAI / Microsoft Foundry) --------------------------------------------------------
resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${prefix}-aoai-${suffix}'
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${prefix}-aoai-${suffix}'
    disableLocalAuth: true // keys are disabled: only Entra ID identities can call the models
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: chatModelName
  sku: { name: 'GlobalStandard', capacity: chatCapacity }
  properties: {
    model: { format: 'OpenAI', name: chatModelName, version: chatModelVersion }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: embeddingModelName
  sku: { name: 'Standard', capacity: embeddingCapacity }
  properties: {
    model: { format: 'OpenAI', name: embeddingModelName, version: embeddingModelVersion }
  }
  dependsOn: [chatDeployment] // deployments on one account must be created one at a time
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, identity.id, roles.openAiUser)
  scope: openai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}

// Hosting -------------------------------------------------------------------------------------------
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var usePlaceholder = startsWith(containerImage, 'mcr.microsoft.com/')

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: usePlaceholder ? 80 : 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: usePlaceholder ? [] : [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: [{ name: 'api-token', keyVaultUrl: apiTokenSecret.properties.secretUri, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'ESG_PROVIDER', value: 'azure' }
            { name: 'ESG_AZURE_ENDPOINT', value: openai.properties.endpoint }
            { name: 'ESG_AZURE_CHAT_DEPLOYMENT', value: chatDeployment.name }
            { name: 'ESG_AZURE_EMBEDDING_DEPLOYMENT', value: embeddingDeployment.name }
            { name: 'ESG_API_TOKEN', secretRef: 'api-token' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // tells DefaultAzureCredential which identity to use
          ]
          probes: usePlaceholder
            ? []
            : [
                {
                  type: 'Liveness'
                  httpGet: { path: '/health', port: 8080 }
                  periodSeconds: 30
                }
              ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 3 }
    }
  }
  dependsOn: [acrPull, kvSecretsUser, openAiUser]
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output registryName string = registry.name
output openAiEndpoint string = openai.properties.endpoint
output keyVaultName string = vault.name
