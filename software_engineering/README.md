# Software engineering docs

UML for NotebookLLM⁻, written in PlantUML. Every `.puml` has its rendered `.png` committed next
to it. The diagrams describe the system as it is now, including agentic answering, reranking,
guardrails and Langfuse. Update them in the same change as the code they describe.

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
| Profiles and notebooks | [01_profiles_and_notebooks](1_user_stories/01_profiles_and_notebooks.png) |
| Sources and ingestion | [02_sources_and_ingest](1_user_stories/02_sources_and_ingest.png) |
| Grounded chat (agentic RAG, reranking) | [03_chat_and_citations](1_user_stories/03_chat_and_citations.png) |
| Guardrails | [04_guardrails](1_user_stories/04_guardrails.png) |
| Memory and Studio | [05_memory_and_studio](1_user_stories/05_memory_and_studio.png) |
| Settings, observability, prompt versions | [06_settings_and_operations](1_user_stories/06_settings_and_operations.png) |

## 2. Use case diagrams — `2_use_case_diagrams/`

| Scope | Diagram |
| --- | --- |
| **Whole system** | [system_overview](2_use_case_diagrams/system_overview.png) |
| Sources and ingestion | [sources_and_ingest](2_use_case_diagrams/sources_and_ingest.png) |
| Chat with guardrails | [chat](2_use_case_diagrams/chat.png) |
| Studio and memory | [studio_and_memory](2_use_case_diagrams/studio_and_memory.png) |
| Operations and observability | [operations](2_use_case_diagrams/operations.png) |

## 3. Sequence diagrams — `3_sequence_diagrams/`

| Flow | Diagram |
| --- | --- |
| **Whole system**, upload to a guarded, cited answer | [system_overview](3_sequence_diagrams/system_overview.png) |
| Chat: guardrails → plan → rerank → parts → synthesis | [chat_agentic_rag](3_sequence_diagrams/chat_agentic_rag.png) |
| Guardrail micro-batching (N items or T seconds) | [guardrail_microbatch](3_sequence_diagrams/guardrail_microbatch.png) |
| Upload → ingest → index | [upload_ingest](3_sequence_diagrams/upload_ingest.png) |
| Link source → fetch | [url_ingest](3_sequence_diagrams/url_ingest.png) |
| Studio generation | [studio_generation](3_sequence_diagrams/studio_generation.png) |
| `/memory` | [memory](3_sequence_diagrams/memory.png) |
| Langfuse tracing and prompt versions | [langfuse_tracing](3_sequence_diagrams/langfuse_tracing.png) |

## 4. Class diagrams — `4_class_diagrams/`

| Scope | Diagram |
| --- | --- |
| **Whole system**, layers and main classes | [system_overview](4_class_diagrams/system_overview.png) |
| Providers: chat, embedding, reranking | [providers](4_class_diagrams/providers.png) |
| Application services | [services](4_class_diagrams/services.png) |
| Data tier: providers, repositories, models | [data_repositories](4_class_diagrams/data_repositories.png) |
| Guardrails (LangChain middleware) and Celery jobs | [guardrails_and_tasks](4_class_diagrams/guardrails_and_tasks.png) |
