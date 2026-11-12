# Documentation

One page per feature that is built, and a placeholder per feature that is planned.

Where the detail lives:

- **[`architecture.md`](architecture.md)**: the tiers, the project structure and the API table.
- **`docs/features/`**: what each feature does, how it works, its settings, API, data, tests and limits.
- **`docs/roadmap/`**: planned work, one placeholder page each.
- **[`software_engineering/`](../software_engineering/README.md)**: UML (user stories, use cases, sequence and class diagrams).
- **`src/.env.example`**: every setting, with the values it accepts. A test fails if one is missing.
- **[`Docker/README.md`](../Docker/README.md)**: the compose layout.
- **[`evals/README.md`](../evals/README.md)**: running the Ragas evaluation and the Locust load test.

## Features

| # | Feature | Page | Main diagrams |
| --- | --- | --- | --- |
| 1 | Accounts and per-user isolation | [01_accounts_and_isolation](features/01_accounts_and_isolation.md) | [sign-in](../software_engineering/3_sequence_diagrams/signup_login.png), [authorization](../software_engineering/3_sequence_diagrams/request_authorization.png) |
| 2 | Notebooks, sources (files and links) and the interface | [02_notebooks_sources_and_ui](features/02_notebooks_sources_and_ui.md) | [link source](../software_engineering/3_sequence_diagrams/url_ingest.png) |
| 3 | Ingestion pipeline and background processing | [03_ingestion_pipeline](features/03_ingestion_pipeline.md) | [upload → index](../software_engineering/3_sequence_diagrams/upload_ingest.png) |
| 4 | OCR and Arabic text repair | [04_ocr_and_arabic_correction](features/04_ocr_and_arabic_correction.md) | |
| 5 | Chunking and citations | [05_chunking_and_citations](features/05_chunking_and_citations.md) | |
| 6 | Storage: documents, embeddings, vector stores | [06_embeddings_and_vector_stores](features/06_embeddings_and_vector_stores.md) | [data tier](../software_engineering/4_class_diagrams/data_repositories.png) |
| 7 | Models and providers | [07_models_and_providers](features/07_models_and_providers.md) | [providers](../software_engineering/4_class_diagrams/providers.png) |
| 8 | Grounded chat and agentic answering | [08_agentic_rag](features/08_agentic_rag.md) | [chat](../software_engineering/3_sequence_diagrams/chat_agentic_rag.png) |
| 9 | Reranking | [09_reranking](features/09_reranking.md) | |
| 10 | Guardrails | [10_guardrails](features/10_guardrails.md) | [micro-batching](../software_engineering/3_sequence_diagrams/guardrail_microbatch.png) |
| 11 | Memory | [11_memory](features/11_memory.md) | [/memory](../software_engineering/3_sequence_diagrams/memory.png) |
| 12 | Studio: mind map, flashcards, quiz | [12_studio](features/12_studio.md) | [generation](../software_engineering/3_sequence_diagrams/studio_generation.png) |
| 13 | Observability, errors and logging | [13_observability](features/13_observability.md) | [tracing](../software_engineering/3_sequence_diagrams/langfuse_tracing.png) |
| 14 | Multimodal retrieval: pictures as sources | [14_multimodal_retrieval](features/14_multimodal_retrieval.md) | [ingest](../software_engineering/3_sequence_diagrams/image_ingest.png), [answer](../software_engineering/3_sequence_diagrams/image_query_answer.png) |
| 15 | Deployment | [15_deployment](features/15_deployment.md) | |
| 16 | Tests and code quality | [16_tests_and_code_quality](features/16_tests_and_code_quality.md) | |
| 17 | Evaluation (Ragas) and load testing (Locust) | [17_evaluation_and_load_testing](features/17_evaluation_and_load_testing.md) | [evaluation and load](../software_engineering/3_sequence_diagrams/evaluation_and_load.png) |

Every feature page has the same sections: **What it does**, **How it works**, **Settings**,
**API**, **Data**, **Tests**, **Diagrams**, **Known limits**. A section is left out when it would
be empty.

## Planned

See [roadmap/](roadmap/README.md).
