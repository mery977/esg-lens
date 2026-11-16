"""Azure OpenAI (Microsoft Foundry) provider.

Authentication is keyless by default: `DefaultAzureCredential` uses the managed identity of the
Container App in Azure and your `az login` session locally. An API key is only used if you set one.
"""

from __future__ import annotations

from ..config import Settings
from .openai_api import OpenAICompatibleProvider


class AzureOpenAIProvider(OpenAICompatibleProvider):
    name = "azure"

    def __init__(self, settings: Settings):
        super().__init__()
        try:
            from openai import AzureOpenAI, OpenAI
        except ImportError as e:  # pragma: no cover
            raise ImportError("pip install 'esg-lens[azure]'") from e
        if not settings.azure_endpoint:
            raise ValueError("ESG_AZURE_ENDPOINT is required for the azure provider")

        if settings.azure_api_key:
            credential = settings.azure_api_key
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            credential = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        if settings.azure_api_version:  # legacy dated API
            key = "api_key" if isinstance(credential, str) else "azure_ad_token_provider"
            self.client = AzureOpenAI(
                azure_endpoint=settings.azure_endpoint,
                api_version=settings.azure_api_version,
                max_retries=0,  # retries are handled once, in LLMProvider
                **{key: credential},
            )
        else:  # v1 API: works with every current model (GPT-5 family included), no api-version
            base_url = settings.azure_endpoint.rstrip("/") + "/openai/v1/"
            self.client = OpenAI(base_url=base_url, api_key=credential, max_retries=0)
        self.chat_model = settings.azure_chat_deployment  # on Azure, "model" is the deployment name
        self.embedding_model = settings.azure_embedding_deployment
