"""The four stages one uploaded document passes through.

`process` plans the ingestion and publishes one `parse` per page batch; each
parse stores its pages and queues a `postprocess`; whichever correction
finishes last claims the run and dispatches `assemble`, which chunks the
document and hands it to indexing. They are together because they are one
pipeline -- the other jobs beside this package (index, studio, memory,
maintenance) each stand alone.

Registration is by module path in `celery_app.py`'s `include=[...]`, and these
modules moved once already. A module missing from that list never registers,
and its queue then accepts messages nothing will ever consume -- silently.
`test_every_job_module_is_registered` is what makes that loud.
"""

from .assemble import assemble_chunks_task
from .parse import parse_batch_task
from .postprocess import postprocess_batch_task
from .process import process_data_task
from .summarise import summarise_chunks_task

__all__ = [
    "assemble_chunks_task",
    "parse_batch_task",
    "postprocess_batch_task",
    "process_data_task",
    "summarise_chunks_task",
]
