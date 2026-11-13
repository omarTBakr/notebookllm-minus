# Software engineering docs

UML for NotebookLLM⁻, written in PlantUML. Every `.puml` has its rendered `.png` committed next
to it. The diagrams describe the system as it is now, including accounts and per-user isolation,
agentic answering, reranking, guardrails, Langfuse, and pictures as searchable sources (multimodal retrieval). Update them in the same change as the code they describe.

The written documentation, one page per feature, is in [`docs/`](../docs/README.md).

## Rendering

```bash
software_engineering/render.sh                       # every diagram
software_engineering/render.sh 4_class_diagrams/providers.puml
```

Rendering needs `java` and graphviz (`dot`). On first run `render.sh` downloads the pinned
PlantUML jar (1.2026.9, checked against its published SHA-256) into `.tools/`, which is
gitignored, so nothing is installed outside the repository. Set `PLANTUML_JAR` to use a
different jar. `_style.iuml` holds the shared look.

## 1. User stories — `1_user_stories/`

| Epic | Diagram |
| --- | --- |
| Account and notebooks | [01_profiles_and_notebooks](1_user_stories/01_profiles_and_notebooks.png) |
| Sources and ingestion | [02_sources_and_ingest](1_user_stories/02_sources_and_ingest.png) |
| Grounded chat (agentic RAG, reranking) | [03_chat_and_citations](1_user_stories/03_chat_and_citations.png) |
| Guardrails | [04_guardrails](1_user_stories/04_guardrails.png) |
| Memory and Studio | [05_memory_and_studio](1_user_stories/05_memory_and_studio.png) |
| Settings, observability, prompt versions | [06_settings_and_operations](1_user_stories/06_settings_and_operations.png) |
| Accounts and privacy | [07_accounts_and_privacy](1_user_stories/07_accounts_and_privacy.png) |
| Pictures as sources (multimodal) | [08_multimodal](1_user_stories/08_multimodal.png) |

## 2. Use case diagrams — `2_use_case_diagrams/`

| Scope | Diagram |
| --- | --- |
| **Whole system** | [system_overview](2_use_case_diagrams/system_overview.png) |
| Sources and ingestion | [sources_and_ingest](2_use_case_diagrams/sources_and_ingest.png) |
| Chat with guardrails | [chat](2_use_case_diagrams/chat.png) |
| Studio and memory | [studio_and_memory](2_use_case_diagrams/studio_and_memory.png) |
| Operations and observability | [operations](2_use_case_diagrams/operations.png) |
| Accounts and privacy | [accounts](2_use_case_diagrams/accounts.png) |
| Pictures (multimodal retrieval) | [multimodal](2_use_case_diagrams/multimodal.png) |

## 3. Sequence diagrams — `3_sequence_diagrams/`

| Flow | Diagram |
| --- | --- |
| **Whole system**, sign in → upload → a guarded, cited answer | [system_overview](3_sequence_diagrams/system_overview.png) |
| Sign up, sign in, sign out | [signup_login](3_sequence_diagrams/signup_login.png) |
| Every data request: session, then ownership | [request_authorization](3_sequence_diagrams/request_authorization.png) |
| Chat: guardrails → plan → rerank → parts → synthesis | [chat_agentic_rag](3_sequence_diagrams/chat_agentic_rag.png) |
| Guardrail micro-batching (N items or T seconds) | [guardrail_microbatch](3_sequence_diagrams/guardrail_microbatch.png) |
| Upload → ingest → index | [upload_ingest](3_sequence_diagrams/upload_ingest.png) |
| Link source → fetch | [url_ingest](3_sequence_diagrams/url_ingest.png) |
| Studio generation | [studio_generation](3_sequence_diagrams/studio_generation.png) |
| `/memory` | [memory](3_sequence_diagrams/memory.png) |
| Langfuse tracing and prompt versions | [langfuse_tracing](3_sequence_diagrams/langfuse_tracing.png) |
| Pictures at ingest: find, caption, chunk, link, embed | [image_ingest](3_sequence_diagrams/image_ingest.png) |
| A question with pictures: both indexes, linked partners, with or without vision | [image_query_answer](3_sequence_diagrams/image_query_answer.png) |
| Evaluation and load: Ragas scores the deployed system, Locust loads it | [evaluation_and_load](3_sequence_diagrams/evaluation_and_load.png) |
| Visualization: Grafana, Flower and RabbitMQ behind the app's session, Langfuse on the open notebook | [visualization](3_sequence_diagrams/visualization.png) |

## 4. Class diagrams — `4_class_diagrams/`

| Scope | Diagram |
| --- | --- |
| **Whole system**, layers and main classes | [system_overview](4_class_diagrams/system_overview.png) |
| Providers: chat, embedding, reranking | [providers](4_class_diagrams/providers.png) |
| Application services | [services](4_class_diagrams/services.png) |
| Data tier: providers, repositories, models | [data_repositories](4_class_diagrams/data_repositories.png) |
| Guardrails (LangChain middleware) and Celery jobs | [guardrails_and_tasks](4_class_diagrams/guardrails_and_tasks.png) |
| Accounts: models, repositories, services, login layer | [accounts](4_class_diagrams/accounts.png) |
| Pictures: embedders, index, captions, regions, links, vision gate | [multimodal](4_class_diagrams/multimodal.png) |
